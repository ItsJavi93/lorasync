"""enter_at_mode() debe confirmar la entrada en modo AT, no darla por hecha."""
import pytest

from common.config import RadioConfig
from common.radio import Radio, RadioError

CFG = RadioConfig(port="fake", addr=20, channel=70)


class _AtSerial:
    """Módulo que ignora los primeros `escapes_ignorados` intentos de '+++' (modo paquete:
    ningún eco, ninguna respuesta) y a partir de ahí responde a los comandos AT."""

    def __init__(self, escapes_ignorados: int = 0):
        self._restantes = escapes_ignorados
        self._in_at = False
        self.inbox = bytearray()

    def write(self, data: bytes):
        if data == b"+++\r\n":
            if self._restantes > 0:
                self._restantes -= 1
            else:
                self._in_at = True
            return
        if self._in_at:
            cmd = data.decode("ascii").strip()
            self.inbox.extend(f"{cmd}\n\r\nOK\r\n".encode("ascii"))

    @property
    def in_waiting(self) -> int:
        return len(self.inbox)

    def read(self, size: int) -> bytes:
        data = bytes(self.inbox[:size])
        del self.inbox[:len(data)]
        return data

    def reset_input_buffer(self):
        self.inbox.clear()

    def close(self):
        pass


def test_entra_a_la_primera():
    radio = Radio(CFG, _AtSerial())
    radio.enter_at_mode()
    assert "OK" in radio.at_query("AT+SF?")


def test_reintenta_con_guarda_larga_desde_modo_paquete():
    radio = Radio(CFG, _AtSerial(escapes_ignorados=1))
    radio.enter_at_mode()  # el primer '+++' se pierde; el reintento entra
    assert "OK" in radio.at_query("AT+SF?")


def test_falla_explicito_si_el_modulo_no_responde():
    radio = Radio(CFG, _AtSerial(escapes_ignorados=99))
    with pytest.raises(RadioError, match="no entró en modo AT"):
        radio.enter_at_mode()


class _ConfigSerial:
    """Simula el módulo aplicando cada AT+PARAM=valor y devolviéndolo tal cual en AT+PARAM?,
    salvo los comandos en `ignorar` -- simulan un parámetro que el módulo no aplicó de verdad
    aunque respondió OK al comando de escritura (p.ej. AT+ADDR con una dirección fuera de rango
    que el firmware acepta pero no guarda)."""

    def __init__(self, ignorar: set[str] = frozenset()):
        self._in_at = False
        self._values: dict[str, str] = {}
        self._ignorar = ignorar
        self.inbox = bytearray()

    def write(self, data: bytes):
        if data == b"+++\r\n":
            self._in_at = True
            return
        if not self._in_at:
            return
        cmd = data.decode("ascii").strip()
        if cmd.endswith("?"):
            key = cmd[:-1]
            self.inbox.extend(f"{cmd}\n\r{self._values.get(key, '0')}\r\nOK\r\n".encode("ascii"))
            return
        key, _, value = cmd.partition("=")
        if key and key not in self._ignorar:
            self._values[key] = value
        self.inbox.extend(f"{cmd}\n\rOK\r\n".encode("ascii"))

    @property
    def in_waiting(self) -> int:
        return len(self.inbox)

    def read(self, size: int) -> bytes:
        data = bytes(self.inbox[:size])
        del self.inbox[:len(data)]
        return data

    def reset_input_buffer(self):
        self.inbox.clear()

    def close(self):
        pass


def test_apply_config_detects_addr_not_really_applied():
    """Solo se releían SF/TXCH/RXCH/BW; si el módulo no guardaba de verdad AT+ADDR (aunque
    respondiera OK), el nodo quedaba escuchando/enviando con una dirección distinta a la
    configurada y nada en el código lo detectaba -- se veía como "no llegan los ACK", sin pista
    de la causa real."""
    cfg = RadioConfig(port="fake", addr=42, channel=70)
    radio = Radio(cfg, _ConfigSerial(ignorar={"AT+ADDR"}))
    with pytest.raises(RadioError, match="AT\\+ADDR"):
        radio.apply_config()


def test_apply_config_passes_when_module_confirms_every_param():
    cfg = RadioConfig(port="fake", addr=42, channel=70)
    radio = Radio(cfg, _ConfigSerial())
    radio.apply_config()  # no debe lanzar
