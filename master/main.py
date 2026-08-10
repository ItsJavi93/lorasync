"""Bucle del maestro: recibir -> validar -> deduplicar -> escribir CSV -> enviar ACK.

La deduplicación real vive en MasterStore (INSERT OR IGNORE por (nodo_id, seq)); el CSV solo
escribe una fila cuando la muestra era nueva, así que un reenvío por ACK perdido no duplica
datos en el CSV tampoco."""
import time

from common.frame import PROTOCOL_VERSION, Frame, FrameError, FrameType, decode_frame, encode_frame, split_stream
from common.radio import Radio
from master.csvsink import CsvSink
from master.store import MasterStore
from node.sampler import decode_batch, encode_sample


def _handle_data_frame(radio: Radio, store: MasterStore, csvsink: CsvSink, fr: Frame,
                        rssi_dbm: float | None, arrival_ts: float) -> None:
    rssi_int = round(rssi_dbm) if rssi_dbm is not None else None
    for i, (ts_nodo, values) in enumerate(decode_batch(fr.payload, fr.n_muestras)):
        seq = fr.seq_inicial + i
        inserted = store.insert_received(fr.id_nodo, seq, arrival_ts, ts_nodo, rssi_int,
                                          encode_sample(values))
        if inserted:
            for variable, valor in values.items():
                csvsink.write_row(arrival_ts, ts_nodo, fr.id_nodo, seq, variable, valor, rssi_int)

    ack_seq = store.get_ultimo_seq_contiguo(fr.id_nodo) or 0
    ack = encode_frame(PROTOCOL_VERSION, FrameType.ACK, fr.id_nodo, ack_seq, 0, b"")
    radio.send(fr.id_nodo, radio.config.channel, ack)


def run_master(radio: Radio, store: MasterStore, csvsink: CsvSink, iterations: int | None = None,
               poll_interval_s: float = 0.05) -> None:
    buf = b""
    count = 0
    while iterations is None or count < iterations:
        buf += radio.read_bytes(timeout_s=poll_interval_s)
        frames, buf = split_stream(buf, radio.config.rssi_append)
        for raw, rssi in frames:
            try:
                fr = decode_frame(raw)
            except FrameError:
                continue  # trama corrupta/truncada: se descarta, nunca excepción sin control
            if fr.tipo == FrameType.DATA:
                _handle_data_frame(radio, store, csvsink, fr, rssi, time.time())
        count += 1


if __name__ == "__main__":
    import sys
    import tomllib

    from common.config import RadioConfig

    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)
    radio_cfg = RadioConfig(**raw["radio"])
    master_cfg = raw.get("master", {})

    with Radio(radio_cfg) as radio, MasterStore(master_cfg.get("db_path", "master.db")) as store, \
            CsvSink(master_cfg.get("csv_dir", "csv")) as csvsink:
        radio.apply_config()
        run_master(radio, store, csvsink)
