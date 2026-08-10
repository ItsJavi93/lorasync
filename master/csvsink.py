"""CSV largo, append-only, con rotación diaria. La cabecera nunca cambia (ver PLAN.md Fase 3)."""
import csv
import datetime as dt
import pathlib

HEADER = ["ts_utc", "ts_nodo", "nodo_id", "seq", "variable", "valor", "rssi"]


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


class CsvSink:
    def __init__(self, directory: str):
        self._dir = pathlib.Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._date: dt.date | None = None
        self._file = None
        self._writer = None

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ensure_open(self, date: dt.date):
        if date == self._date:
            return
        self.close()
        path = self._dir / f"telemetria-{date.isoformat()}.csv"
        is_new = not path.exists()
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(HEADER)
            self._file.flush()
        self._date = date

    def write_row(self, ts_utc: float, ts_nodo: float, nodo_id: int, seq: int,
                   variable: str, valor: float, rssi: int | None):
        date = dt.datetime.fromtimestamp(ts_utc, tz=dt.timezone.utc).date()
        self._ensure_open(date)
        self._writer.writerow([_iso(ts_utc), _iso(ts_nodo), nodo_id, seq, variable, valor, rssi])
        self._file.flush()
