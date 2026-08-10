"""Consola AT interactiva para configurar y diagnosticar dongles a mano.

Uso: python -m tools.at_console [puerto]
Si no se pasa puerto, se detecta automáticamente. Escribe comandos AT y ENTER;
'exit' o Ctrl-C para salir.
"""
import sys

import serial

from common.radio import BAUDRATE, GUARD_TIME_S, find_dongle_port


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_dongle_port()
    if not port:
        print("No se detectó ningún dongle. Especifica el puerto manualmente.")
        return 1

    print(f"Conectando a {port} @ {BAUDRATE} 8N1...")
    ser = serial.Serial(port, BAUDRATE, timeout=1.0)
    import time

    time.sleep(GUARD_TIME_S)
    ser.write(b"+++\r\n")
    time.sleep(GUARD_TIME_S)
    print("Modo AT activo. Escribe comandos (ej: AT+VER). 'exit' para salir.")

    try:
        while True:
            cmd = input("AT> ").strip()
            if cmd.lower() == "exit":
                break
            if not cmd:
                continue
            ser.reset_input_buffer()
            ser.write((cmd + "\r\n").encode("ascii"))
            time.sleep(1.0)
            resp = ser.read(ser.in_waiting or 1).decode("ascii", errors="replace").strip()
            print(resp)
    except KeyboardInterrupt:
        pass
    finally:
        ser.write(b"AT+EXIT\r\n")
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
