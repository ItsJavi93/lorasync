"""Exporta el registro completo de un nodo (node.db) a CSV largo, confirmado y pendiente.

Uso (en la Pi, se puede correr con node.main en marcha):
  python -m tools.exportar_nodo node.db nodo.csv

Columnas: ts_nodo, seq, variable, valor, confirmado (1 = el maestro confirmó que la tiene).
Mismo formato largo y mismos seq que el CSV del maestro, para cruzarlos si falla alguno de los dos.
"""
import csv
import pathlib
import sqlite3
import sys

from master.csvsink import _iso
from node.sampler import decode_sample

HEADER = ["ts_nodo", "seq", "variable", "valor", "confirmado"]


def exportar(db_path: str, csv_path: str) -> int:
    """Escribe el registro en csv_path y devuelve cuántas muestras exportó.

    Abre node.db en solo lectura: no crea una base vacía si la ruta está mal, ni compite por
    escribir con un node.main que esté corriendo (WAL permite leer mientras tanto)."""
    ruta = pathlib.Path(db_path)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe {db_path}")
    conn = sqlite3.connect(ruta.resolve().as_uri() + "?mode=ro", uri=True)
    n = 0
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)
            filas = conn.execute("SELECT seq, ts_utc, payload, confirmado FROM muestras ORDER BY seq")
            for seq, ts_utc, payload, confirmado in filas:
                for variable, valor in decode_sample(payload).items():
                    writer.writerow([_iso(ts_utc), seq, variable, valor, confirmado])
                n += 1
    finally:
        conn.close()
    return n


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    total = exportar(sys.argv[1], sys.argv[2])
    print(f"{total} muestras exportadas a {sys.argv[2]}")
