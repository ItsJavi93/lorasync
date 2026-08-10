"""Almacén del nodo: SQLite en modo WAL. seq usa AUTOINCREMENT para que nunca se reutilice,
ni siquiera si la tabla queda completamente vacía tras purgar (sin AUTOINCREMENT, SQLite
reasigna desde 1 en ese caso, violando la garantía de secuencia monótona)."""
import sqlite3
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS muestras (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc REAL NOT NULL,
    payload BLOB NOT NULL,
    confirmado INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pendientes ON muestras(confirmado, seq);
"""


@dataclass
class Sample:
    seq: int
    ts_utc: float
    payload: bytes


class NodeStore:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def add_sample(self, ts_utc: float, payload: bytes) -> int:
        """Persiste antes de transmitir: confirma con commit() antes de devolver el control."""
        cur = self._conn.execute(
            "INSERT INTO muestras (ts_utc, payload) VALUES (?, ?)", (ts_utc, payload)
        )
        self._conn.commit()
        return cur.lastrowid

    def get_pending(self, limit: int | None = None) -> list[Sample]:
        sql = "SELECT seq, ts_utc, payload FROM muestras WHERE confirmado=0 ORDER BY seq"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [Sample(*row) for row in rows]

    def mark_confirmed_up_to(self, seq: int):
        """Confirmación acumulativa: un solo número confirma todo lo anterior."""
        self._conn.execute("UPDATE muestras SET confirmado=1 WHERE seq <= ?", (seq,))
        self._conn.commit()

    def purge_confirmed(self) -> int:
        cur = self._conn.execute("DELETE FROM muestras WHERE confirmado=1")
        self._conn.commit()
        return cur.rowcount
