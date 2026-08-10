"""Prueba manual con 2 dongles: enviar o recibir 'hola' en modo paquete.

Uso:
  python -m tools.hello_test send <config.toml> <dest_addr> [dest_channel]
                                  [--repeat N] [--interval S] [--skip-config]
  python -m tools.hello_test recv <config.toml> [timeout_s] [--skip-config]

La configuración vive en la flash del módulo y sobrevive al apagado, así que
`--skip-config` evita los ~4 s en modo AT durante los cuales la radio está sorda.
Arranca siempre el receptor antes que el emisor.
"""
import argparse
import sys
import time

from common.config import load_config
from common.radio import Radio

PAYLOAD = b"hola"


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("config", help="ruta al config.toml de esta máquina")
    common.add_argument(
        "--skip-config",
        action="store_true",
        help="no reaplicar la configuración; el módulo ya la tiene en flash",
    )

    parser = argparse.ArgumentParser(prog="python -m tools.hello_test", description=__doc__)
    sub = parser.add_subparsers(dest="role", required=True)

    send = sub.add_parser("send", parents=[common], help="enviar 'hola'")
    send.add_argument("dest_addr", type=int)
    send.add_argument("dest_channel", type=int, nargs="?", help="por defecto, el canal propio")
    send.add_argument("--repeat", type=int, default=1, help="número de paquetes (por defecto 1)")
    send.add_argument("--interval", type=float, default=1.0, help="segundos entre paquetes")

    recv = sub.add_parser("recv", parents=[common], help="escuchar y volcar lo que llegue")
    recv.add_argument("timeout_s", type=float, nargs="?", default=30.0)

    return parser


def _do_send(radio: Radio, cfg, args) -> int:
    dest_channel = args.dest_channel if args.dest_channel is not None else cfg.channel
    for i in range(args.repeat):
        radio.send(args.dest_addr, dest_channel, PAYLOAD)
        print(f"[{_stamp()}] tx {i + 1}/{args.repeat} {PAYLOAD!r} -> addr={args.dest_addr} ch={dest_channel}")
        if i + 1 < args.repeat:
            time.sleep(args.interval)
    return 0


def _do_recv(radio: Radio, cfg, args) -> int:
    print(f"[{_stamp()}] escuchando {args.timeout_s:.0f} s en canal {cfg.channel} como addr={cfg.addr}...")
    deadline = time.monotonic() + args.timeout_s
    total = bytearray()
    # Volcado crudo en vez de exigir un tamaño exacto: así también se ven tramas
    # truncadas o basura, que con una lectura de tamaño fijo aparecerían como un
    # simple timeout sin ninguna pista de qué llegó.
    while time.monotonic() < deadline:
        chunk = radio.read_bytes(256, timeout_s=0.5)
        if not chunk:
            continue
        total += chunk
        print(f"[{_stamp()}] rx {len(chunk)} B: {chunk!r}")
        if cfg.rssi_append:
            print(f"    último byte como RSSI: {-chunk[-1] / 2:.1f} dBm")
    if not total:
        print(f"[{_stamp()}] nada recibido en {args.timeout_s:.0f} s.")
        return 1
    print(f"[{_stamp()}] fin: {len(total)} B en total, {PAYLOAD!r} {'presente' if PAYLOAD in total else 'AUSENTE'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config(args.config)

    with Radio(cfg) as radio:
        if not args.skip_config:
            t0 = time.monotonic()
            radio.apply_config()
            print(f"[{_stamp()}] configuración aplicada en {time.monotonic() - t0:.1f} s")
        return _do_send(radio, cfg, args) if args.role == "send" else _do_recv(radio, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
