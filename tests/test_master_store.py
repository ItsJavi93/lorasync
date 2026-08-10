from master.store import MasterStore


def test_duplicate_insert_does_not_duplicate_row(tmp_path):
    store = MasterStore(str(tmp_path / "master.db"))
    first = store.insert_received(10, 1, 100.0, 99.5, -73, b"a")
    second = store.insert_received(10, 1, 101.0, 99.5, -73, b"a")
    assert first is True
    assert second is False
    count = store._conn.execute(
        "SELECT COUNT(*) FROM recibidas WHERE nodo_id=10 AND seq=1"
    ).fetchone()[0]
    assert count == 1
    store.close()


def test_ultimo_seq_contiguo_advances_with_no_gaps(tmp_path):
    store = MasterStore(str(tmp_path / "master.db"))
    store.insert_received(10, 1, 0.0, 0.0, -70, b"x")
    store.insert_received(10, 2, 0.0, 0.0, -70, b"x")
    store.insert_received(10, 3, 0.0, 0.0, -70, b"x")
    assert store.get_ultimo_seq_contiguo(10) == 3
    store.close()


def test_ultimo_seq_contiguo_never_passes_a_gap(tmp_path):
    store = MasterStore(str(tmp_path / "master.db"))
    store.insert_received(10, 1, 0.0, 0.0, -70, b"x")
    store.insert_received(10, 2, 0.0, 0.0, -70, b"x")
    store.insert_received(10, 5, 0.0, 0.0, -70, b"x")  # hueco: faltan 3 y 4
    assert store.get_ultimo_seq_contiguo(10) == 2
    store.insert_received(10, 3, 0.0, 0.0, -70, b"x")
    assert store.get_ultimo_seq_contiguo(10) == 3  # sigue sin poder pasar del hueco en 4
    store.close()
