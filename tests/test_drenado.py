"""Drenado intercalado tras un corte: la cola vieja sale en orden, lo recién medido sale sin
esperar a que termine, y ningún lote "nuevo" repite muestras que otro lote nuevo ya envió."""
import re
import threading
import time

from common.config import RadioConfig
from common.radio import Radio
from common.schedule import AlohaScheduler
from master.csvsink import CsvSink
from master.main import run_master
from master.store import MasterStore
from node.main import _next_batch, run_node
from node.sampler import SAMPLE_PAYLOAD_LEN, decode_batch, decode_sample, encode_batch, encode_sample
from node.store import NodeStore, Sample
from tests.test_e2e_simulado import _make_pair

_PAYLOAD = b"\x00" * SAMPLE_PAYLOAD_LEN


def _cola(n: int, primer_seq: int = 1) -> list[Sample]:
    """Cola contigua a 1 Hz."""
    return [Sample(primer_seq + i, 1_000_000.0 + i, _PAYLOAD) for i in range(n)]


def _cola_tras_reinicio(nuevas: int) -> list[Sample]:
    """Como en hardware (2026-09-16): 79 muestras viejas (seq 313-391), corte de 1 h y `nuevas`
    muestras recién tomadas a 1 Hz (seq 392 en adelante)."""
    viejas = [Sample(313 + i, 1_000_000.0 + i, _PAYLOAD) for i in range(79)]
    recientes = [Sample(392 + i, 1_003_600.0 + i, _PAYLOAD) for i in range(nuevas)]
    return viejas + recientes


def _seqs(batch):
    return [s.seq for s in batch]


def test_turno_viejo_manda_el_lote_mas_antiguo():
    batch, fue_nuevo = _next_batch(_cola(30), 10, prefer_newest=False)
    assert _seqs(batch) == list(range(1, 11)) and not fue_nuevo


def test_turno_nuevo_manda_las_10_mas_recientes_sin_enviar():
    batch, fue_nuevo = _next_batch(_cola(30), 10, prefer_newest=True, enviado_nuevo_hasta=15)
    assert _seqs(batch) == list(range(21, 31)) and fue_nuevo


def test_turno_nuevo_no_repite_lo_que_otro_lote_nuevo_ya_mando():
    """Antes: 1808-1817, 1810-1819, 1812-1821... cada lote repetía 8 de 10 muestras."""
    batch, fue_nuevo = _next_batch(_cola(30), 10, prefer_newest=True, enviado_nuevo_hasta=21)
    # solo 9 sin enviar (22-30): no llenan un lote, así que el turno pasa a la cola vieja
    assert _seqs(batch) == list(range(1, 11)) and not fue_nuevo


def test_turno_nuevo_cede_si_tras_el_hueco_hay_pocas_muestras_recientes():
    batch, fue_nuevo = _next_batch(_cola_tras_reinicio(nuevas=2), 10, prefer_newest=True)
    assert _seqs(batch) == list(range(313, 323)) and not fue_nuevo


def test_turno_nuevo_no_cruza_el_hueco_temporal():
    batch, fue_nuevo = _next_batch(_cola_tras_reinicio(nuevas=15), 10, prefer_newest=True)
    assert _seqs(batch) == list(range(397, 407)) and fue_nuevo
    payload = encode_batch(batch[0].ts_utc, [(s.ts_utc, s.payload) for s in batch])
    assert len(decode_batch(payload, len(batch))) == len(batch)


def _valores(seq: int) -> dict[str, float]:
    """Distintos por seq, para detectar muestras sobrescritas o mezcladas."""
    return {"temp_c": float(seq), "volt": 12.0, "hum_pct": 50.0, "press_hpa": 1000.0 + seq}


def _perder_cada(ser, n: int):
    """Descarta 1 de cada n escrituras: trama perdida en el aire."""
    orig, cuenta = ser.write, [0]

    def write(data):
        cuenta[0] += 1
        if cuenta[0] % n:
            orig(data)
    ser.write = write


def _drenar(tmp_path, capsys, perder_datos: int = 0, perder_acks: int = 0):
    """Nodo con 79 muestras viejas sin confirmar (seq 313-391, de hace 1 h) contra un maestro que
    ya tiene 1-312. El nodo sigue muestreando mientras vacía el atraso."""
    node_db, master_db = str(tmp_path / "node.db"), str(tmp_path / "master.db")
    with NodeStore(node_db) as st:
        st.resync_desde(312)
        hace_1h = time.time() - 3600
        for i in range(79):
            st.add_sample(hace_1h + i, encode_sample(_valores(313 + i)))
    with MasterStore(master_db) as st:
        for seq in range(1, 313):
            st.insert_received(20, seq, 0.0, 0.0, -26, encode_sample(_valores(seq)))

    contador = iter(range(392, 10_000))
    node_ser, master_ser = _make_pair()
    if perder_datos:
        _perder_cada(node_ser, perder_datos)
    if perder_acks:
        _perder_cada(master_ser, perder_acks)
    node_radio = Radio(RadioConfig(port="sim", addr=20, channel=70), serial_port=node_ser)
    master_radio = Radio(RadioConfig(port="sim", addr=10, channel=70), serial_port=master_ser)

    def node_worker():
        with NodeStore(node_db) as st:
            run_node(node_radio, st, 10, AlohaScheduler(0, 0.002),
                      sample_fn=lambda: _valores(next(contador)), batch_size=10,
                      ack_timeout_s=0.3, sample_interval_s=0.002, flush_interval_s=0.05,
                      iterations=60)

    def master_worker():
        with MasterStore(master_db) as st, CsvSink(str(tmp_path / "csv")) as cs:
            run_master(master_radio, st, cs, iterations=3000, poll_interval_s=0.002)

    hilos = [threading.Thread(target=node_worker), threading.Thread(target=master_worker)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=60)
    assert not any(h.is_alive() for h in hilos)

    enviados = [tuple(map(int, m)) for m in re.findall(r"TX seq (\d+)-(\d+)", capsys.readouterr().out)]
    with NodeStore(node_db) as st:
        pendientes = st.get_pending()
    with MasterStore(master_db) as st:
        guardadas = dict(st._conn.execute("SELECT seq, payload FROM recibidas WHERE nodo_id=20"))
    return enviados, pendientes, guardadas, 391 + 60  # último seq muestreado


def test_la_cola_vieja_llega_completa_y_sin_alterar_aunque_se_pierdan_tramas(tmp_path, capsys):
    _, pendientes, guardadas, ultimo = _drenar(tmp_path, capsys, perder_datos=3, perder_acks=4)
    assert pendientes == []
    assert sorted(guardadas) == list(range(1, ultimo + 1))
    assert all(decode_sample(p)["temp_c"] == float(s) for s, p in guardadas.items())


def test_ningun_lote_nuevo_repite_muestras_de_otro_lote_nuevo(tmp_path, capsys):
    """Sin pérdidas, solo se reenvía algo cuando la cola vieja alcanza muestras que ya salieron
    en un lote nuevo, nunca entre lotes nuevos. Los lotes nuevos son los que no empiezan donde
    terminó el lote viejo anterior."""
    enviados, pendientes, guardadas, ultimo = _drenar(tmp_path, capsys)
    assert pendientes == [] and sorted(guardadas) == list(range(1, ultimo + 1))

    siguiente_viejo, nuevos = 313, []
    for ini, fin in enviados:
        if ini == siguiente_viejo:
            siguiente_viejo = fin + 1
        else:
            nuevos.append((ini, fin))
    for (_, fin_a), (ini_b, _) in zip(nuevos, nuevos[1:]):
        assert ini_b > fin_a, f"lotes nuevos solapados: {nuevos}"
