"""El nodo no debe purgar su cola con un ACK que confirme seq que nunca llegó a enviar.

Escenario real que provocó pérdida silenciosa de datos: se borró node.db en la Pi pero no
master.db en el maestro. Los seq del nodo volvieron a empezar en 1 y chocaron con los 1222 que
el maestro ya tenía guardados de la ejecución anterior. El maestro descartó las muestras nuevas
como duplicadas ("0 nuevas de 10") y respondió "ACK hasta 1222"; el nodo purgó con eso toda su
cola. Ni el nodo ni el maestro registraban nada raro.
"""
import threading

from common.config import RadioConfig
from common.radio import Radio
from common.schedule import AlohaScheduler
from master.csvsink import CsvSink
from master.main import run_master
from master.store import MasterStore
from node.main import _ack_de_otra_encarnacion, run_node
from node.store import NodeStore
from tests.test_e2e_simulado import _make_pair, _make_sampler


class _Ack:
    def __init__(self, seq_inicial):
        self.seq_inicial = seq_inicial


def test_ack_por_encima_de_lo_enviado_se_detecta():
    assert _ack_de_otra_encarnacion(_Ack(1222), max_seq_enviado=10)


def test_ack_dentro_de_lo_enviado_es_valido():
    # el ACK acumulativo puede ir por detrás (lote anterior) o justo al día, nunca por delante
    assert not _ack_de_otra_encarnacion(_Ack(10), max_seq_enviado=10)
    assert not _ack_de_otra_encarnacion(_Ack(4), max_seq_enviado=10)
    assert not _ack_de_otra_encarnacion(None, max_seq_enviado=10)


def test_nodo_renumerado_no_pierde_la_cola_contra_un_maestro_con_historial(tmp_path):
    """Nodo con seq desde 1 contra un maestro que ya guardó hasta 1222 del mismo id_nodo."""
    node_ser, master_ser = _make_pair()
    node_radio = Radio(RadioConfig(port="sim", addr=20, channel=70, rssi_append=True),
                        serial_port=node_ser)
    master_radio = Radio(RadioConfig(port="sim", addr=10, channel=70, rssi_append=True),
                          serial_port=master_ser)

    node_db = str(tmp_path / "node.db")
    master_db = str(tmp_path / "master.db")

    # maestro con el historial de la ejecución anterior del mismo nodo
    with MasterStore(master_db) as store:
        for seq in range(1, 1223):
            store.insert_received(20, seq, 0.0, 0.0, -26, b"x" * 16)
        assert store.get_ultimo_seq_contiguo(20) == 1222

    n_muestras = 12

    def node_worker():
        with NodeStore(node_db) as store:  # node.db nuevo: los seq arrancan en 1
            run_node(node_radio, store, master_addr=10, scheduler=AlohaScheduler(0, 0.01),
                      sample_fn=_make_sampler(), batch_size=10, ack_timeout_s=0.5,
                      sample_interval_s=0.005, flush_interval_s=0.05, iterations=n_muestras)

    def master_worker():
        with MasterStore(master_db) as store, CsvSink(str(tmp_path / "csv")) as csvsink:
            run_master(master_radio, store, csvsink, iterations=300, poll_interval_s=0.01)

    hilos = [threading.Thread(target=node_worker), threading.Thread(target=master_worker)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=15)
    assert not any(h.is_alive() for h in hilos)

    # Las muestras no llegaron a guardarse (el maestro las tiró como duplicadas), así que TIENEN
    # que seguir en la cola del nodo. Purgarlas sería perderlas para siempre.
    with NodeStore(node_db) as store:
        assert len(store.get_pending()) == n_muestras
