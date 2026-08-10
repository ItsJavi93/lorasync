"""Driver del dongle Waveshare USB-TO-LoRa-HF: modo AT, transmisión/recepción en modo paquete."""
import glob
import sys
import time

import serial
from serial.tools import list_ports

from common.config import RadioConfig

GUARD_TIME_S = 0.2
BAUDRATE = 115200
AT_TIMEOUT_S = 1.0  # espera máxima por la respuesta completa a un comando AT
AT_POLL_S = 0.02  # intervalo de sondeo de bytes entrantes
AT_QUIET_S = 0.2  # silencio tras el último byte que da la respuesta por terminada
AT_ESCAPE_GUARD_S = 1.0  # guarda del reintento de '+++' cuando el módulo viene en modo paquete

_AT_PARAMS = {
    "sf": "AT+SF={}",
    "bw_code": "AT+BW={}",
    "cr": "AT+CR={}",
    "power_dbm": "AT+PWR={}",
    "channel_tx": "AT+TXCH={}",
    "channel_rx": "AT+RXCH={}",
    "addr": "AT+ADDR={}",
    "netid": "AT+NETID={}",
}


class RadioError(RuntimeError):
    pass


def find_dongle_port() -> str | None:
    """Detección automática: primer puerto serie disponible en Windows (COM*) o Linux (/dev/ttyUSB*)."""
    if sys.platform.startswith("win"):
        ports = [p.device for p in list_ports.comports()]
        return ports[0] if ports else None
    candidates = sorted(glob.glob("/dev/ttyUSB*"))
    return candidates[0] if candidates else None


class Radio:
    def __init__(self, config: RadioConfig, serial_port: serial.SerialBase | None = None):
        self.config = config
        self._ser = serial_port or serial.Serial(config.port, BAUDRATE, timeout=AT_TIMEOUT_S)
        self._in_at_mode = False

    def close(self):
        self._ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- modo AT ----

    def enter_at_mode(self):
        """Entra en modo AT y lo confirma con AT+VER; reintenta con una guarda más larga.

        Un módulo que viene en modo paquete (AT+MODE=2, que persiste en flash entre arranques)
        no hace eco de nada, así que un `+++` a ciegas dejaba al driver creyendo estar en modo
        AT mientras todos los comandos posteriores devolvían cadena vacía -- el fallo aparecía
        más tarde y disfrazado ("el módulo no confirmó la configuración aplicada").
        La guarda de 0,2 s basta con el módulo ya en modo AT, pero no siempre para salir de
        modo paquete; con 1,0 s sí.
        """
        # ponytail: en algunos dongles (visto con chip CH343 en Windows) reentrar a modo AT
        # tras un exit_at_mode() previo no responde, incluso reabriendo el puerto. Usa un
        # Radio por sesión de comandos AT en vez de version()+apply_config() encadenados.
        if self._in_at_mode:
            return
        for guard in (GUARD_TIME_S, AT_ESCAPE_GUARD_S):
            time.sleep(guard)
            self._ser.reset_input_buffer()
            self._ser.write(b"+++\r\n")
            time.sleep(guard)
            self._in_at_mode = True
            if "OK" in self._send_at("AT+VER"):
                return
            self._in_at_mode = False
        raise RadioError(
            f"El módulo en {self.config.port} no entró en modo AT: '+++' sin respuesta con "
            f"guardas de {GUARD_TIME_S}s y {AT_ESCAPE_GUARD_S}s. Comprueba el puerto (el dongle "
            f"es el 1a86 de /dev/serial/by-id/, no el GPS), el baudrate ({BAUDRATE}) y la "
            f"alimentación de la Pi (Fase 1 nota 8)."
        )

    def exit_at_mode(self):
        if not self._in_at_mode:
            return
        self._send_at("AT+EXIT")
        self._in_at_mode = False

    def _send_at(self, cmd: str) -> str:
        """Envía un comando AT y espera hasta AT_QUIET_S de silencio tras el último byte.

        El módulo hace eco del comando antes de responder, así que la respuesta llega en
        varias ráfagas; esperar silencio las captura todas sin pagar AT_TIMEOUT_S completo
        por comando (una secuencia de configuración pasa de ~16 s a ~4 s).
        """
        self._ser.reset_input_buffer()
        self._ser.write((cmd + "\r\n").encode("ascii"))
        deadline = time.monotonic() + AT_TIMEOUT_S
        buf = bytearray()
        last_byte_at = time.monotonic()
        while time.monotonic() < deadline:
            pending = self._ser.in_waiting
            if pending:
                buf += self._ser.read(pending)
                last_byte_at = time.monotonic()
            elif buf and time.monotonic() - last_byte_at >= AT_QUIET_S:
                break
            time.sleep(AT_POLL_S)
        return buf.decode("ascii", errors="replace").strip()

    def at_query(self, cmd: str) -> str:
        """Envía un comando AT (en modo AT) y devuelve la respuesta cruda."""
        if not self._in_at_mode:
            raise RadioError("at_query requiere estar en modo AT (enter_at_mode primero)")
        return self._send_at(cmd)

    # ---- configuración ----

    def apply_config(self):
        """Aplica la configuración validada al módulo y confirma releyendo cada valor."""
        self.enter_at_mode()
        try:
            self._send_at(_AT_PARAMS["sf"].format(self.config.sf))
            self._send_at(_AT_PARAMS["bw_code"].format(self.config.bw_code))
            self._send_at(_AT_PARAMS["cr"].format(self.config.cr))
            self._send_at(_AT_PARAMS["power_dbm"].format(self.config.power_dbm))
            self._send_at(_AT_PARAMS["channel_tx"].format(self.config.channel))
            self._send_at(_AT_PARAMS["channel_rx"].format(self.config.channel))
            self._send_at(_AT_PARAMS["addr"].format(self.config.addr))
            self._send_at(_AT_PARAMS["netid"].format(self.config.netid))
            self._send_at(f"AT+LBT={1 if self.config.lbt else 0}")
            self._send_at(f"AT+RSSI={1 if self.config.rssi_append else 0}")
            self._verify_config()
            self._send_at("AT+MODE=2")
        finally:
            self.exit_at_mode()

    def _verify_config(self):
        checks = {
            "AT+SF?": str(self.config.sf),
            "AT+TXCH?": str(self.config.channel),
            "AT+RXCH?": str(self.config.channel),
            "AT+BW?": str(self.config.bw_code),
        }
        mismatches = []
        for query, expected in checks.items():
            actual = self._send_at(query)
            if expected not in actual:
                mismatches.append((query, expected, actual))
        if mismatches:
            raise RadioError(f"El módulo no confirmó la configuración aplicada: {mismatches}")

    def version(self) -> str:
        self.enter_at_mode()
        try:
            return self._send_at("AT+VER")
        finally:
            self.exit_at_mode()

    # ---- datos (modo paquete) ----

    def send(self, dest_addr: int, dest_channel: int, payload: bytes):
        """Modo paquete: los 3 primeros bytes son [dir_alta][dir_baja][canal] del destino."""
        header = bytes([(dest_addr >> 8) & 0xFF, dest_addr & 0xFF, dest_channel & 0xFF])
        self._ser.write(header + payload)
        self._ser.flush()  # el proceso puede cerrar el puerto justo después de enviar

    def read_bytes(self, max_bytes: int = 4096, timeout_s: float | None = None) -> bytes:
        """Lee hasta `max_bytes` disponibles sin exigir un tamaño exacto, para tramas
        delimitadas (COBS) cuyo tamaño no se conoce de antemano. Con un timeout corto se
        comporta como un poll: devuelve enseguida lo que haya, o vacío si no llegó nada."""
        if timeout_s is not None:
            self._ser.timeout = timeout_s
        return self._ser.read(max_bytes)

    def receive(self, size: int, timeout_s: float | None = None) -> tuple[bytes, float | None]:
        """Lee `size` bytes de datos. Si rssi_append está activo, separa y devuelve el byte de RSSI final.

        RSSI en dBm = -byte_rssi / 2 (registro RssiInst del SX1262, pasos de 0.5 dB;
        confirmado empíricamente: byte-256 daba valores imposibles como -205 dBm).
        """
        if timeout_s is not None:
            self._ser.timeout = timeout_s
        n = size + 1 if self.config.rssi_append else size
        raw = self._ser.read(n)
        if len(raw) < n:
            raise RadioError(f"Timeout: se esperaban {n} bytes, llegaron {len(raw)}")
        if self.config.rssi_append:
            data, rssi_byte = raw[:-1], raw[-1]
            rssi_dbm = -rssi_byte / 2
            return data, rssi_dbm
        return raw, None
