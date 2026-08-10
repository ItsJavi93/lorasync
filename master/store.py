"""Almacén del maestro: deduplicación por (nodo_id, seq) y último_seq_contiguo por nodo."""
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recibidas (
    nodo_id INTEGER, seq INTEGER, ts_utc REAL, ts_nodo REAL,
    rssi INTEGER, payload BLOB,
    PRIMARY KEY (nodo_id, seq)
);
CREATE TABLE IF NOT EXISTS estado_nodo (
    nodo_id INTEGER PRIMARY KEY,
    ultimo_seq_contiguo INTEGER,
    visto_por_ultima_vez REAL
);
"""


class MasterStore:
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

    def insert_received(self, nodo_id: int, seq: int, ts_utc: float, ts_nodo: float,
                         rssi: int | None, payload: bytes) -> bool:
        """Inserta una muestra recibida (ignora duplicados) y avanza ultimo_seq_contiguo.
        Devuelve False si (nodo_id, seq) ya existía."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO recibidas (nodo_id, seq, ts_utc, ts_nodo, rssi, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nodo_id, seq, ts_utc, ts_nodo, rssi, payload),
        )
        inserted = cur.rowcount > 0
        self._advance_contiguo(nodo_id, ts_utc)
        self._conn.commit()
        return inserted

    def _advance_contiguo(self, nodo_id: int, seen_at: float):
        row = self._conn.execute(
            "SELECT ultimo_seq_contiguo FROM estado_nodo WHERE nodo_id=?", (nodo_id,)
        ).fetchone()
        contiguo = row[0] if row else 0
        while self._conn.execute(
            "SELECT 1 FROM recibidas WHERE nodo_id=? AND seq=?", (nodo_id, contiguo + 1)
        ).fetchone():
            contiguo += 1
        self._conn.execute(
            "INSERT INTO estado_nodo (nodo_id, ultimo_seq_contiguo, visto_por_ultima_vez) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(nodo_id) DO UPDATE SET "
            "ultimo_seq_contiguo=excluded.ultimo_seq_contiguo, "
            "visto_por_ultima_vez=excluded.visto_por_ultima_vez",
            (nodo_id, contiguo, seen_at),
        )

    def get_ultimo_seq_contiguo(self, nodo_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT ultimo_seq_contiguo FROM estado_nodo WHERE nodo_id=?", (nodo_id,)
        ).fetchone()
        return row[0] if row else None
