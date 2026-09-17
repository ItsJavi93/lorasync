import csv

import pytest

from node.sampler import encode_sample
from node.store import NodeStore
from tools.exportar_nodo import exportar


def _valores(seq: int) -> dict[str, float]:
    return {"temp_c": 20.5 + seq, "volt": 12.0, "hum_pct": 50.0, "press_hpa": 1000.0 + seq}


def test_exporta_todo_el_registro_confirmado_y_pendiente(tmp_path):
    db = str(tmp_path / "node.db")
    with NodeStore(db) as store:
        for seq in range(1, 4):
            store.add_sample(1_758_000_000.0 + seq, encode_sample(_valores(seq)))
        store.mark_confirmed_up_to(2)

        # con el nodo todavía corriendo (conexión abierta), igual que en la Pi
        n = exportar(db, str(tmp_path / "nodo.csv"))

    assert n == 3
    filas = list(csv.DictReader(open(tmp_path / "nodo.csv", encoding="utf-8")))
    assert len(filas) == 3 * 4
    por_seq = {}
    for f in filas:
        por_seq.setdefault(int(f["seq"]), {})[f["variable"]] = f
    assert sorted(por_seq) == [1, 2, 3]
    for seq, variables in por_seq.items():
        assert float(variables["temp_c"]["valor"]) == 20.5 + seq
        assert float(variables["press_hpa"]["valor"]) == 1000.0 + seq
        assert {v["confirmado"] for v in variables.values()} == {"1" if seq <= 2 else "0"}
    assert filas[0]["ts_nodo"] == "2025-09-16T05:20:01.000Z"


def test_no_crea_una_base_vacia_si_la_ruta_no_existe(tmp_path):
    with pytest.raises(FileNotFoundError):
        exportar(str(tmp_path / "no_existe.db"), str(tmp_path / "nodo.csv"))
    assert not (tmp_path / "no_existe.db").exists()
