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
