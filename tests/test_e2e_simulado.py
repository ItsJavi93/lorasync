"""Nodo y maestro conectados por puertos serie falsos, corriendo en hilos separados (como dos
procesos reales), para probar el ciclo completo sin hardware: envío -> ACK -> purga, con
reintento cuando el maestro tarda en arrancar (simula un corte) y sin huecos ni duplicados."""
import threading
import time

from common.config import RadioConfig
from common.radio import Radio
from common.schedule import AlohaScheduler
from master.csvsink import CsvSink
from master.main import run_master
from master.store import MasterStore
from node.main import run_node
from node.store import NodeStore


class _FakeSerial:
    """Simula el módulo LoRa en modo paquete: los 3 bytes de cabecera de destino los consume
    el firmware (no viajan como datos) y el firmware receptor añade sus 2 bytes de RSSI.
    Este fake añadía uno solo, y con eso el enlace real fallaba desde la segunda trama mientras
    la suite entera seguía en verde: el fake mentía sobre el hardware."""

    def __init__(self, rssi_byte: int = 46):
        self.inbox = bytearray()
        self.timeout = 0.05
        self._rssi_byte = rssi_byte
        self._lock = threading.Lock()
        self.peer: "_FakeSerial | None" = None

    def write(self, data: bytes):
        with self.peer._lock:
            self.peer.inbox.extend(data[3:])
            # 2 bytes literales, no RSSI_SUFFIX_LEN: el fake imita al hardware, no al parser.
            # Y no idénticos, porque en hardware oscilan ±1 entre sí.
            self.peer.inbox.extend([self._rssi_byte, self._rssi_byte + 1])

    def read(self, size: int) -> bytes:
        """Bloquea hasta `self.timeout` esperando datos, como pyserial real -- necesario para
        que los bucles de nodo/maestro se sincronicen de verdad entre hilos (si devolviera
        vacío al instante, el maestro agotaría sus `iterations` antes de que el nodo termine
        de reenviar)."""
        deadline = time.time() + self.timeout
        while True:
            with self._lock:
                if self.inbox:
                    data = bytes(self.inbox[:size])
                    del self.inbox[:len(data)]
                    return data
            if time.time() >= deadline:
                return b""
            time.sleep(0.001)

    @property
    def in_waiting(self) -> int:
        return len(self.inbox)

    def reset_input_buffer(self):
        with self._lock:
            self.inbox.clear()

    def flush(self):
        """No-op: la escritura al par ya es síncrona. Existe porque Radio.send() la llama."""


def _make_pair() -> tuple[_FakeSerial, _FakeSerial]:
    a, b = _FakeSerial(), _FakeSerial()
    a.peer, b.peer = b, a
    return a, b


def _make_sampler():
    counter = iter(range(10_000))

    def sample():
        i = next(counter)
        return {"temp_c": 20.0 + i, "volt": 12.0, "hum_pct": 50.0, "press_hpa": 1013.0}

    return sample


def test_ciclo_completo_sin_huecos_ni_duplicados(tmp_path):
    node_ser, master_ser = _make_pair()
    node_cfg = RadioConfig(port="sim", addr=20, channel=70, rssi_append=True)
    master_cfg = RadioConfig(port="sim", addr=10, channel=70, rssi_append=True)
    node_radio = Radio(node_cfg, serial_port=node_ser)
    master_radio = Radio(master_cfg, serial_port=master_ser)

    node_db = str(tmp_path / "node.db")
    master_db = str(tmp_path / "master.db")
    csv_dir = str(tmp_path / "csv")

    n_muestras = 23  # no múltiplo de 10: deja un resto que debe salir por flush_interval

    def node_worker():
        with NodeStore(node_db) as store:
            run_node(node_radio, store, master_addr=10, scheduler=AlohaScheduler(0, 0.01),
                      sample_fn=_make_sampler(), batch_size=10, ack_timeout_s=0.5,
                      sample_interval_s=0.005, flush_interval_s=0.05, iterations=n_muestras)

    def master_worker():
        with MasterStore(master_db) as store, CsvSink(csv_dir) as csvsink:
            run_master(master_radio, store, csvsink, iterations=300, poll_interval_s=0.01)

    # el maestro "tarda en arrancar" (corte simulado): el nodo ya habrá encolado y reintentado
    # el primer lote antes de que el maestro empiece a escuchar.
    node_thread = threading.Thread(target=node_worker)
    master_thread = threading.Thread(target=master_worker)
    node_thread.start()
    node_thread.join(timeout=0.15)
    master_thread.start()
    node_thread.join(timeout=10)
    master_thread.join(timeout=10)

    assert not node_thread.is_alive()
    assert not master_thread.is_alive()

    with NodeStore(node_db) as store:
        assert store.get_pending() == []  # todo confirmado y purgado, nada varado

    with MasterStore(master_db) as store:
        assert store.get_ultimo_seq_contiguo(20) == n_muestras  # sin huecos

    rows = (tmp_path / "csv").glob("telemetria-*.csv")
    lines = []
    for path in rows:
        lines.extend(path.read_text(encoding="utf-8").splitlines()[1:])  # sin cabecera
    # 23 muestras x 4 variables = 92 filas, ni una de más (dedup) ni una de menos (sin huecos)
    assert len(lines) == n_muestras * 4
    seqs_por_variable: dict[str, set[int]] = {}
    for line in lines:
        _, _, nodo_id, seq, variable, _valor, _rssi = line.split(",")
        assert nodo_id == "20"
        seqs_por_variable.setdefault(variable, set()).add(int(seq))
    for variable, seqs in seqs_por_variable.items():
        assert seqs == set(range(1, n_muestras + 1)), f"huecos/duplicados en {variable}"
