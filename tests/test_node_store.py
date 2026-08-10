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


def test_purge_never_deletes_unconfirmed(tmp_path):
    store = NodeStore(str(tmp_path / "node.db"))
    seqs = [store.add_sample(float(i), b"x") for i in range(3)]
    store.mark_confirmed_up_to(seqs[0])
    deleted = store.purge_confirmed()
    assert deleted == 1
    remaining = {p.seq for p in store.get_pending()}
    assert remaining == set(seqs[1:])
    store.close()


def test_seq_never_reused_even_after_full_purge(tmp_path):
    store = NodeStore(str(tmp_path / "node.db"))
    seq1 = store.add_sample(1.0, b"x")
    store.mark_confirmed_up_to(seq1)
    store.purge_confirmed()  # tabla queda vacía
    seq2 = store.add_sample(2.0, b"y")
    assert seq2 > seq1
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
