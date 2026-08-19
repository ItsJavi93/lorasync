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

    def resync_desde(self, seq_base: int) -> int:
        """Renumera la cola para que arranque en seq_base+1, y deja el contador ahí.

        Hace falta cuando el maestro conserva la numeración de una ejecución anterior de este
        nodo (node.db borrado, SD nueva, Pi reemplazada): los seq locales vuelven a empezar en 1
        y chocan por número con muestras distintas que el maestro ya guardó, así que su
        INSERT OR IGNORE las descarta y nunca las almacena. Saltar por encima de su último seq
        las convierte otra vez en nuevas para él.

        Se renumera la cola entera, incluido lo que todavía no se había entregado, así que no se
        pierde ni una muestra. Devuelve el nuevo seq máximo (0 si no hubo nada que mover).
        """
        min_seq, max_seq = self._conn.execute(
            "SELECT MIN(seq), MAX(seq) FROM muestras"
        ).fetchone()
        if min_seq is None:
            nuevo_max = seq_base  # cola vacía: basta con mover el contador
        else:
            delta = seq_base + 1 - min_seq
            if delta <= 0:
                return 0  # ya estamos por encima; idempotente ante un ACK repetido
            # Sin colisión de PRIMARY KEY: delta > (max_seq - min_seq), así que todo seq
            # desplazado cae por encima del máximo actual.
            self._conn.execute("UPDATE muestras SET seq = seq + ?", (delta,))
            nuevo_max = max_seq + delta
        # AUTOINCREMENT saca el siguiente seq de sqlite_sequence, no del contenido de la tabla:
        # sin esto, las muestras nuevas volverían a la numeración vieja y chocarían otra vez.
        cur = self._conn.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = 'muestras'", (nuevo_max,)
        )
        if cur.rowcount == 0:  # aún no se ha insertado nada, la fila no existe
            self._conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('muestras', ?)", (nuevo_max,)
            )
        self._conn.commit()
        return nuevo_max
