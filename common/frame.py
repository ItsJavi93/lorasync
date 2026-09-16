"""Formato de trama a prueba de corrupción: cabecera + CRC16 + COBS.

[ver(1) tipo(1) id_nodo(2) seq_inicial(4) n_muestras(1) payload(N) crc16(2)] -> COBS -> + 0x00

Enteros multibyte en big-endian. El CRC16 cubre todo menos sí mismo. COBS elimina cualquier
0x00 del contenido, así que el 0x00 final es un delimitador inequívoco: los bytes de RSSI que
AT+RSSI=1 añade al final de cada paquete recibido caen después de ese delimitador.
"""
import struct
from dataclasses import dataclass
from enum import IntEnum

_HEADER_FMT = ">BBHIB"  # ver, tipo, id_nodo, seq_inicial, n_muestras
_HEADER_LEN = struct.calcsize(_HEADER_FMT)
_CRC_LEN = 2

PROTOCOL_VERSION = 1

# Límite duro del módulo LoRa (firmware DTU): un paquete de más de esto se envía igual, pero
# sale truncado o corrupto sin ningún error a nivel de firmware (PLAN.md Fase 3, "Límite del
# módulo"). batch_size o número de variables altos lo superan en silencio si nada lo revisa aquí.
MODULE_MAX_FRAME_LEN = 240

# Bytes que el módulo añade tras CADA paquete recibido con AT+RSSI=1. Medido en hardware
# (tools.hello_test): 'hola' (4 B) sale del dongle como 6 B, b'hola\x33\x34', y los dos
# valores oscilan juntos entre 51 y 52 (-25.5 y -26.0 dBm) con los módulos pegados.
# Descontar solo 1 dejaba el segundo byte pegado al frente de la trama siguiente: la primera
# trama del enlace decodificaba bien y TODAS las posteriores morían como "bloque COBS
# truncado", descartadas en silencio.
RSSI_SUFFIX_LEN = 2


class FrameType(IntEnum):
    DATA = 0
    ACK = 1
    HELLO = 2
    TIME = 3


class FrameError(ValueError):
    pass


@dataclass
class Frame:
    ver: int
    tipo: FrameType
    id_nodo: int
    seq_inicial: int
    n_muestras: int
    payload: bytes


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF. Elección arbitraria interna al proyecto
    (no hay protocolo externo que exigir una variante concreta), pero es la más habitual en
    enlaces de radio/serie."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    """Consistent Overhead Byte Stuffing. No incluye el delimitador 0x00 final."""
    output = bytearray([0])
    code_idx = 0
    code = 1
    for byte in data:
        if byte == 0:
            output[code_idx] = code
            code_idx = len(output)
            output.append(0)
            code = 1
        else:
            output.append(byte)
            code += 1
            if code == 0xFF:
                output[code_idx] = code
                code_idx = len(output)
                output.append(0)
                code = 1
    output[code_idx] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    output = bytearray()
    idx = 0
    n = len(data)
    while idx < n:
        code = data[idx]
        if code == 0:
            raise FrameError("byte 0x00 inesperado dentro de un bloque COBS")
        idx += 1
        end = idx + code - 1
        if end > n:
            raise FrameError("bloque COBS truncado")
        output += data[idx:end]
        idx = end
        if code < 0xFF and idx < n:
            output.append(0)
    return bytes(output)


def encode_frame(ver: int, tipo: FrameType, id_nodo: int, seq_inicial: int, n_muestras: int,
                  payload: bytes) -> bytes:
    header = struct.pack(_HEADER_FMT, ver, tipo, id_nodo, seq_inicial, n_muestras)
    body = header + payload
    body += struct.pack(">H", crc16_ccitt_false(body))
    encoded = cobs_encode(body) + b"\x00"
    if len(encoded) > MODULE_MAX_FRAME_LEN:
        raise FrameError(
            f"trama de {len(encoded)} B excede el límite de {MODULE_MAX_FRAME_LEN} B del módulo "
            f"(firmware DTU); reduce batch_size o el número de variables por muestra"
        )
    return encoded


def decode_frame(raw: bytes) -> Frame:
    """Decodifica una trama. Tolera bytes extra después del 0x00 (p.ej. el byte de RSSI).
    Lanza FrameError ante cualquier trama truncada o corrupta, nunca una excepción sin control."""
    delim = raw.find(b"\x00")
    if delim == -1:
        raise FrameError("trama truncada: falta el delimitador 0x00")
    body = cobs_decode(raw[:delim])
    if len(body) < _HEADER_LEN + _CRC_LEN:
        raise FrameError("trama demasiado corta para contener cabecera + CRC")
    payload, crc_bytes = body[_HEADER_LEN:-_CRC_LEN], body[-_CRC_LEN:]
    expected_crc = struct.unpack(">H", crc_bytes)[0]
    actual_crc = crc16_ccitt_false(body[:-_CRC_LEN])
    if actual_crc != expected_crc:
        raise FrameError(f"CRC inválido: esperado {expected_crc:#06x}, calculado {actual_crc:#06x}")
    ver, tipo, id_nodo, seq_inicial, n_muestras = struct.unpack(_HEADER_FMT, body[:_HEADER_LEN])
    return Frame(ver, FrameType(tipo), id_nodo, seq_inicial, n_muestras, payload)


def split_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Divide un buffer en tramas completas (terminadas en 0x00) y el resto incompleto, para
    manejar lecturas de puerto serie que traen varias tramas concatenadas o una a medias."""
    parts = buf.split(b"\x00")
    complete = [p + b"\x00" for p in parts[:-1]]
    return complete, parts[-1]


def split_stream(buf: bytes, rssi_append: bool = False) -> tuple[list[tuple[bytes, float | None]], bytes]:
    """Como split_frames, pero para un flujo continuo real del puerto serie con AT+RSSI=1
    activo: cada trama va seguida de RSSI_SUFFIX_LEN bytes que el firmware inserta y que NO son
    parte de la siguiente trama COBS, así que split_frames (que solo separa por 0x00) los
    confundiría. Devuelve (trama_sin_rssi, rssi_dbm) por cada trama completa, más el resto sin
    consumir."""
    frames: list[tuple[bytes, float | None]] = []
    idx = 0
    n = len(buf)
    while True:
        delim = buf.find(b"\x00", idx)
        if delim == -1:
            break
        end = delim + 1
        rssi_dbm = None
        if rssi_append:
            if end + RSSI_SUFFIX_LEN > n:
                break  # faltan todavía bytes del sufijo de RSSI
            # ponytail: se toma el primero de los dos, el pegado al paquete. A distancia cero
            # los dos valen casi lo mismo (51/52) y no hay forma de distinguirlos; si uno
            # resulta ser el RSSI del canal en vez del paquete, se ve alejando el nodo: el del
            # paquete cae con la distancia, el del canal se queda en el ruido de fondo.
            rssi_dbm = -buf[end] / 2
            end += RSSI_SUFFIX_LEN
        frames.append((buf[idx:delim + 1], rssi_dbm))
        idx = end
    return frames, buf[idx:]
