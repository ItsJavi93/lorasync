import pathlib
import subprocess
import sys

from node.store import NodeStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_add_and_get_pending_in_seq_order(tmp_path):
    store = NodeStore(str(tmp_path / "node.db"))
    seqs = [store.add_sample(float(i), f"m{i}".encode()) for i in range(5)]
    pending = store.get_pending()
    assert [p.seq for p in pending] == seqs
    store.close()


def test_confirmar_saca_de_la_cola_pero_conserva_el_registro(tmp_path):
    """node.db es también el registro permanente del nodo: lo confirmado deja de estar pendiente,
    pero nunca se borra (respaldo si falla el maestro después de confirmar)."""
    store = NodeStore(str(tmp_path / "node.db"))
    seqs = [store.add_sample(float(i), f"m{i}".encode()) for i in range(3)]
    store.mark_confirmed_up_to(seqs[0])
    assert [p.seq for p in store.get_pending()] == seqs[1:]
    filas = store._conn.execute("SELECT seq, payload, confirmado FROM muestras ORDER BY seq").fetchall()
    assert filas == [(seqs[0], b"m0", 1), (seqs[1], b"m1", 0), (seqs[2], b"m2", 0)]
    store.close()


def test_confirmar_no_reescribe_lo_ya_confirmado(tmp_path):
    """Con el historial creciendo, cada ACK reescribiría miles de filas ya confirmadas: lento y
    desgasta la SD. Solo deben tocarse las que pasan de pendiente a confirmada."""
    store = NodeStore(str(tmp_path / "node.db"))
    seqs = [store.add_sample(float(i), b"x") for i in range(100)]
    store.mark_confirmed_up_to(seqs[89])
    antes = store._conn.total_changes
    store.mark_confirmed_up_to(seqs[99])
    assert store._conn.total_changes - antes == 10
    store.close()


def test_durability_survives_os_exit_mid_write(tmp_path):
    db_path = str(tmp_path / "node.db")
    script = f"""
import sys, os
sys.path.insert(0, {str(PROJECT_ROOT)!r})
from node.store import NodeStore
s = NodeStore({db_path!r})
for i in range(5):
    s.add_sample(float(i), ("sample-" + str(i)).encode())
s._conn.execute("INSERT INTO muestras (ts_utc, payload) VALUES (99, ?)", (b"uncommitted",))
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", script], check=False)

    store = NodeStore(db_path)
    pending = store.get_pending()
    assert [p.payload for p in pending] == [("sample-" + str(i)).encode() for i in range(5)]
    store.close()


def test_resync_renumera_solo_la_cola_y_respeta_el_historial(tmp_path):
    store = NodeStore(str(tmp_path / "node.db"))
    for i in range(8):
        store.add_sample(float(i), f"m{i}".encode())
    store.mark_confirmed_up_to(5)  # historial 1-5, cola 6-8

    assert store.resync_desde(1222) == 1225
    filas = store._conn.execute("SELECT seq, payload, confirmado FROM muestras ORDER BY seq").fetchall()
    assert filas == [(1, b"m0", 1), (2, b"m1", 1), (3, b"m2", 1), (4, b"m3", 1), (5, b"m4", 1),
                     (1223, b"m5", 0), (1224, b"m6", 0), (1225, b"m7", 0)]
    assert store.add_sample(9.0, b"m8") == 1226
    store.close()


def test_resync_con_una_cola_larga_no_choca_con_sus_propios_seq(tmp_path):
    """Cola 1-30 y el maestro pide seguir desde 11: el destino (11-40) se solapa con seq que
    todavía existen (11-30). Desplazar fila a fila violaría la PRIMARY KEY a mitad del UPDATE."""
    store = NodeStore(str(tmp_path / "node.db"))
    for i in range(30):
        store.add_sample(float(i), f"m{i}".encode())

    assert store.resync_desde(10) == 40
    filas = store._conn.execute("SELECT seq, payload FROM muestras ORDER BY seq").fetchall()
    assert filas == [(11 + i, f"m{i}".encode()) for i in range(30)]
    store.close()
