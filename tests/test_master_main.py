from common.frame import PROTOCOL_VERSION, Frame, FrameType, encode_frame
from master.csvsink import CsvSink
from master.main import _handle_data_frame, run_master
from master.store import MasterStore
from node.sampler import encode_batch, encode_sample


class _FakeRadio:
    def __init__(self):
        self.config = type("cfg", (), {"channel": 70, "rssi_append": False})()
        self.sent = []

    def send(self, dest_addr, dest_channel, payload):
        self.sent.append((dest_addr, dest_channel, payload))


def _make_frame(seq_inicial: int = 1) -> Frame:
    payload = encode_batch(100.0, [(100.0, encode_sample({"temp_c": 21.0, "volt": 12.0,
                                                            "hum_pct": 50.0, "press_hpa": 1013.0}))])
    return Frame(ver=1, tipo=FrameType.DATA, id_nodo=20, seq_inicial=seq_inicial, n_muestras=1,
                 payload=payload)


def test_csv_write_happens_before_db_commit_for_a_new_sample(tmp_path):
    """Si el maestro muere justo tras comprometer en master.db pero antes de escribir el CSV, el
    reenvío del nodo (mismo seq, sin ACK) queda descartado por el dedup de master.db y esa fila
    nunca llega al CSV -- pérdida silenciosa y permanente del entregable real. El CSV debe
    escribirse antes del commit en DB: el peor caso ante un crash pasa a ser una fila duplicada
    (filtrable por seq), no una fila perdida para siempre."""
    store = MasterStore(str(tmp_path / "master.db"))
    csvsink = CsvSink(str(tmp_path / "csv"))
    calls = []
    orig_write_row, orig_insert = csvsink.write_row, store.insert_received
    csvsink.write_row = lambda *a, **k: (calls.append("csv"), orig_write_row(*a, **k))[1]
    store.insert_received = lambda *a, **k: (calls.append("db"), orig_insert(*a, **k))[1]

    _handle_data_frame(_FakeRadio(), store, csvsink, _make_frame(), rssi_dbm=-70, arrival_ts=100.0)

    assert calls[0] == "csv", f"orden real: {calls} -- el CSV debe escribirse antes del commit en DB"
    store.close()
    csvsink.close()


def test_resend_without_crash_still_does_not_duplicate_csv_row(tmp_path):
    """Guardarraíl de no-regresión: un reenvío normal por ACK perdido (sin crash de por medio)
    debe seguir sin duplicar filas en el CSV, tal como documenta master/csvsink.py."""
    store = MasterStore(str(tmp_path / "master.db"))
    csvsink = CsvSink(str(tmp_path / "csv"))
    fr = _make_frame()

    _handle_data_frame(_FakeRadio(), store, csvsink, fr, rssi_dbm=-70, arrival_ts=100.0)
    _handle_data_frame(_FakeRadio(), store, csvsink, fr, rssi_dbm=-70, arrival_ts=101.0)  # reenvío

    store.close()
    csvsink.close()
    lines = []
    for path in (tmp_path / "csv").glob("telemetria-*.csv"):
        lines.extend(path.read_text(encoding="utf-8").splitlines()[1:])
    assert len(lines) == 4  # 1 muestra x 4 variables, ni una fila de más


def test_run_master_survives_frame_with_valid_crc_but_wrong_n_muestras(tmp_path):
    """n_muestras que no coincide con el tamaño real del payload no lo detecta el CRC (que cubre
    exactamente los bytes tal cual llegaron: protege contra corrupción en el aire, no contra un
    desajuste de codificación). decode_batch() revienta con struct.error; run_master() debe
    seguir viva y descartar la trama, no morir con el proceso y cortar la telemetría de todos
    los demás nodos."""
    payload = b"\x00" * 8  # ts_base, sin ninguna muestra real
    raw = encode_frame(PROTOCOL_VERSION, FrameType.DATA, id_nodo=20, seq_inicial=1,
                        n_muestras=5, payload=payload)  # miente: dice 5 muestras, no hay ninguna

    class _OneShotRadio(_FakeRadio):
        def __init__(self, chunk):
            super().__init__()
            self._chunks = [chunk, b""]

        def read_bytes(self, max_bytes=4096, timeout_s=None):
            return self._chunks.pop(0) if self._chunks else b""

    store = MasterStore(str(tmp_path / "master.db"))
    csvsink = CsvSink(str(tmp_path / "csv"))
    run_master(_OneShotRadio(raw), store, csvsink, iterations=2, poll_interval_s=0)  # no debe lanzar
    store.close()
    csvsink.close()
