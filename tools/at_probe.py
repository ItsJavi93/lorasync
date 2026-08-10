"""Sonda de diagnóstico: encuentra en qué puerto y a qué baudrate responde el dongle en modo AT.

Uso: python -m tools.at_probe [puerto ...]
Sin argumentos, prueba todos los /dev/ttyACM*, /dev/ttyUSB* y COM* detectados.
Imprime una línea por combinación puerto/baudrate y la respuesta cruda a AT+VER.
"""
import glob
import sys
import time

import serial
from serial.tools import list_ports

from common.radio import AT_QUIET_S, GUARD_TIME_S

BAUDRATES = [115200, 9600, 57600, 38400, 19200]


def probe(port: str, baud: int) -> str:
    with serial.Serial(port, baud, timeout=0.5) as ser:
        time.sleep(GUARD_TIME_S)
        ser.reset_input_buffer()
        ser.write(b"+++\r\n")
        time.sleep(GUARD_TIME_S)
        ser.reset_input_buffer()
        ser.write(b"AT+VER\r\n")
        time.sleep(AT_QUIET_S * 3)
        return ser.read(ser.in_waiting or 1).decode("ascii", errors="replace").strip()


def main():
    ports = sys.argv[1:] or sorted(
        glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    ) or [p.device for p in list_ports.comports()]
    if not ports:
        print("No se detectó ningún puerto serie.")
        return 1

    for port in ports:
        for baud in BAUDRATES:
            try:
                resp = probe(port, baud)
            except (serial.SerialException, OSError) as e:
                print(f"{port} @ {baud}: ERROR {e}")
                break
            marca = "  <-- RESPONDE" if "Ver" in resp or "OK" in resp else ""
            print(f"{port} @ {baud}: {resp!r}{marca}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
