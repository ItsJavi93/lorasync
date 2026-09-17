"""Almacén del nodo: SQLite en modo WAL. Es a la vez la cola de envío (confirmado=0) y el
registro permanente de todo lo medido: lo confirmado por el maestro nunca se borra, para tener
respaldo en el nodo si después falla el maestro. Exportar con `python -m tools.exportar_nodo`.

seq usa AUTOINCREMENT para que nunca se reutilice (sin él, SQLite reasigna desde el máximo
actual si se borran filas, violando la garantía de secuencia monótona)."""
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
        """Confirmación acumulativa: un solo número confirma todo lo anterior. Solo toca filas
        pendientes: con el historial conservado, reescribir las ya confirmadas en cada ACK sería
        O(historial) y desgastaría la SD."""
        self._conn.execute(
            "UPDATE muestras SET confirmado=1 WHERE confirmado=0 AND seq <= ?", (seq,)
        )
        self._conn.commit()

    def resync_desde(self, seq_base: int) -> int:
        """Renumera la cola para que arranque en seq_base+1, y deja el contador ahí.

        Hace falta cuando el maestro conserva la numeración de una ejecución anterior de este
        nodo (node.db borrado, SD nueva, Pi reemplazada): los seq locales vuelven a empezar en 1
        y chocan por número con muestras distintas que el maestro ya guardó, así que su
        INSERT OR IGNORE las descarta y nunca las almacena. Saltar por encima de su último seq
        las convierte otra vez en nuevas para él.

        Se renumera la cola pendiente entera, incluido lo que todavía no se había entregado, así
        que no se pierde ni una muestra. El historial ya confirmado NO se toca: si se desplazara,
        el maestro esperaría esos seq y el nodo nunca los reenviaría (ya están confirmados), con lo
        que su ACK se quedaría clavado para siempre. Devuelve el nuevo seq máximo (0 si no hubo
        nada que mover).
        """
        min_pend, max_pend = self._conn.execute(
            "SELECT MIN(seq), MAX(seq) FROM muestras WHERE confirmado=0"
        ).fetchone()
        if min_pend is None:
            # sin cola: basta con mover el contador, sin bajarlo por debajo del historial
            max_total = self._conn.execute("SELECT MAX(seq) FROM muestras").fetchone()[0] or 0
            nuevo_max = max(seq_base, max_total)
        else:
            delta = seq_base + 1 - min_pend
            if delta <= 0:
                return 0  # ya estamos por encima; idempotente ante un ACK repetido
            # En dos pasos, pasando por negativos: el destino puede solaparse con seq pendientes
            # que aún no se han movido (cola 1-30 hacia 11-40), y un UPDATE directo violaría la
            # PRIMARY KEY a mitad de camino. El historial queda por debajo del destino, porque
            # solo hay resync si el maestro confirma más allá de lo que el nodo ha enviado.
            self._conn.execute("UPDATE muestras SET seq = -seq WHERE confirmado=0")
            self._conn.execute("UPDATE muestras SET seq = ? - seq WHERE seq < 0", (delta,))
            nuevo_max = max_pend + delta
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
