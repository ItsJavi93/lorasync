"""Bucle del nodo: muestrear -> persistir -> agrupar -> transmitir -> escuchar ACK -> purgar.

Envía en cuanto hay `batch_size` muestras pendientes, o cuando pasan `flush_interval_s`
segundos desde el último envío (para no dejar un resto de <10 muestras varado indefinidamente).
Si hay más de `batch_size` pendientes (nodo puesto al día tras un corte), alterna entre el lote
más antiguo y el más reciente en cada envío (drenado intercalado 1:1, PLAN.md Fase 3)."""
import time

from common.frame import PROTOCOL_VERSION, FrameError, FrameType, decode_frame, encode_frame, split_stream
from common.radio import Radio
from common.schedule import Scheduler
from node.sampler import encode_batch, encode_sample, read_sample, trim_to_span
from node.store import NodeStore, Sample


def _next_batch(pending: list[Sample], batch_size: int, prefer_newest: bool) -> list[Sample]:
    if len(pending) <= batch_size:
        return trim_to_span(pending)
    return trim_to_span(pending[-batch_size:] if prefer_newest else pending[:batch_size])


def _ack_de_otra_encarnacion(ack, max_seq_enviado: int) -> bool:
    """Un ACK acumulativo no puede confirmar más allá de lo que este nodo llegó a enviar.

    Si lo hace, viene de un maestro que todavía recuerda la numeración de un nodo anterior:
    node.db borrado o Pi reemplazada, con el master.db del otro lado intacto. Los seq vuelven a
    empezar en 1 y chocan por número con los ya guardados, que son datos distintos. El maestro
    los tira por su INSERT OR IGNORE ("0 nuevas de 10") y responde con su seq contiguo viejo,
    muy por encima; el nodo purga con él toda la cola. Sin esta guarda no queda rastro de la
    pérdida en ninguno de los dos lados: el nodo cree haber entregado y el maestro nunca
    guardó nada."""
    return ack is not None and ack.seq_inicial > max_seq_enviado


def _log_resync(ack, max_seq_enviado: int, nuevo_max: int) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] RESYNC: el maestro confirma hasta seq "
          f"{ack.seq_inicial} y este nodo solo ha enviado hasta {max_seq_enviado}. Conserva la "
          f"numeración de una ejecución anterior, así que descartaba las muestras nuevas como "
          f"duplicadas. Cola renumerada por encima: ahora llega hasta seq {nuevo_max}. No se "
          f"pierde ninguna muestra; se reenvían todas.", flush=True)


def _restantes(pending: list[Sample], ack) -> int:
    """Cola que queda DESPUÉS de aplicar el ACK. Se calcula sobre la lista ya leída en vez de
    volver a consultar el store: en este punto nadie más ha insertado nada (mismo hilo)."""
    if ack is None:
        return len(pending)
    return sum(1 for s in pending if s.seq > ack.seq_inicial)


def _log_tx(batch: list[Sample], ack, pendientes: int) -> None:
    """`pendientes` es la cola tras purgar, no antes. Loguear el valor previo daba siempre
    batch_size -- el envío se dispara justo al alcanzarlo -- y parecía una cola clavada."""
    estado = f"ACK hasta {ack.seq_inicial}" if ack is not None else "SIN ACK"
    print(f"[{time.strftime('%H:%M:%S')}] TX seq {batch[0].seq}-{batch[-1].seq} "
          f"({len(batch)} muestras), {estado}, {pendientes} pendientes", flush=True)


def _build_frame(id_nodo: int, batch: list[Sample]) -> bytes:
    ts_base = batch[0].ts_utc
    payload = encode_batch(ts_base, [(s.ts_utc, s.payload) for s in batch])
    return encode_frame(PROTOCOL_VERSION, FrameType.DATA, id_nodo, batch[0].seq, len(batch), payload)


def _wait_for_ack(radio: Radio, buf: bytes, timeout_s: float, own_addr: int):
    deadline = time.time() + timeout_s
    while True:
        frames, buf = split_stream(buf, radio.config.rssi_append)
        for raw, _rssi in frames:
            try:
                fr = decode_frame(raw)
            except FrameError:
                continue
            if fr.tipo == FrameType.ACK and fr.id_nodo == own_addr:
                return fr, buf
        remaining = deadline - time.time()
        if remaining <= 0:
            return None, buf
        buf += radio.read_bytes(timeout_s=min(0.05, remaining))


def run_node(radio: Radio, store: NodeStore, master_addr: int, scheduler: Scheduler,
             sample_fn=read_sample, batch_size: int = 10, ack_timeout_s: float = 3.0,
             sample_interval_s: float = 1.0, flush_interval_s: float = 10.0,
             iterations: int | None = None) -> None:
    """ack_timeout_s debe cubrir la latencia real del ACK, no solo el tiempo de aire (~80 ms
    para un lote de 10 con SF7/BW500). Con AT+LBT=1 el módulo del maestro retrasa el ACK hasta
    2 s escuchando el canal antes de emitir, así que 1 s se quedaba corto SIEMPRE: el ACK
    llegaba tarde, se leía en el ciclo siguiente y la cola se quedaba clavada un lote por
    detrás para siempre, reenviando muestras ya confirmadas."""
    recv_buf = b""
    last_flush = time.time()
    prefer_newest = False
    max_seq_enviado = 0

    count = 0
    while iterations is None or count < iterations:
        ts = time.time()
        store.add_sample(ts, encode_sample(sample_fn()))

        pending = store.get_pending()
        now = time.time()
        if pending and (len(pending) >= batch_size or now - last_flush >= flush_interval_s):
            batch = _next_batch(pending, batch_size, prefer_newest)
            if len(pending) > batch_size:
                prefer_newest = not prefer_newest
            last_flush = now

            scheduler.wait_for_slot()
            radio.send(master_addr, radio.config.channel, _build_frame(radio.config.addr, batch))
            max_seq_enviado = max(max_seq_enviado, batch[-1].seq)

            ack, recv_buf = _wait_for_ack(radio, recv_buf, ack_timeout_s, radio.config.addr)
            if _ack_de_otra_encarnacion(ack, max_seq_enviado):
                # Se resincroniza solo: en competencia no hay quién entre a borrar las bases de
                # datos a mano, así que quedarse parado avisando no sirve de nada.
                _log_resync(ack, max_seq_enviado, store.resync_desde(ack.seq_inicial))
                max_seq_enviado = ack.seq_inicial
                ack = None  # no confirma nada nuestro; el lote siguiente ya sale renumerado
            if ack is not None:
                store.mark_confirmed_up_to(ack.seq_inicial)
                store.purge_confirmed()
            _log_tx(batch, ack, _restantes(pending, ack))

        time.sleep(sample_interval_s)
        count += 1

    # Si el bucle es acotado (iterations no es None -- pruebas, o un cierre ordenado del
    # proceso), no deja un resto de <batch_size muestras varado esperando al próximo
    # flush_interval_s que ya no llegará: drena la cola completa antes de devolver control.
    if iterations is not None:
        _drain_all(radio, store, master_addr, scheduler, ack_timeout_s, batch_size, recv_buf,
                    max_seq_enviado)


def _drain_all(radio: Radio, store: NodeStore, master_addr: int, scheduler: Scheduler,
                ack_timeout_s: float, batch_size: int, recv_buf: bytes,
                max_seq_enviado: int = 0, max_attempts: int = 5):
    attempts = 0
    while attempts < max_attempts:
        pending = store.get_pending(limit=batch_size)
        if not pending:
            return
        pending = trim_to_span(pending)
        scheduler.wait_for_slot()
        radio.send(master_addr, radio.config.channel, _build_frame(radio.config.addr, pending))
        max_seq_enviado = max(max_seq_enviado, pending[-1].seq)
        ack, recv_buf = _wait_for_ack(radio, recv_buf, ack_timeout_s, radio.config.addr)
        if _ack_de_otra_encarnacion(ack, max_seq_enviado):
            _log_resync(ack, max_seq_enviado, store.resync_desde(ack.seq_inicial))
            max_seq_enviado = ack.seq_inicial
            attempts += 1  # gasta un intento: garantiza que este bucle de cierre termina
            continue  # la cola cambió de numeración: se relee antes de reintentar
        _log_tx(pending, ack, _restantes(pending, ack))
        if ack is None:
            attempts += 1
            continue
        store.mark_confirmed_up_to(ack.seq_inicial)
        store.purge_confirmed()
        attempts = 0


if __name__ == "__main__":
    import sys
    import tomllib

    from common.config import RadioConfig
    from common.schedule import AlohaScheduler

    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)
    radio_cfg = RadioConfig(**raw["radio"])
    node_cfg = raw.get("node", {})

    with Radio(radio_cfg) as radio, NodeStore(node_cfg.get("db_path", "node.db")) as store:
        radio.apply_config()
        run_node(
            radio, store, node_cfg.get("master_addr", 10),
            AlohaScheduler(max_delay_s=node_cfg.get("aloha_max_delay_s", 2.0)),
            batch_size=node_cfg.get("batch_size", 10),
            ack_timeout_s=node_cfg.get("ack_timeout_s", 3.0),
            sample_interval_s=node_cfg.get("sample_interval_s", 1.0),
            flush_interval_s=node_cfg.get("flush_interval_s", 10.0),
        )
