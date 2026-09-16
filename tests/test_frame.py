import pytest

from common.frame import (
    RSSI_SUFFIX_LEN, Frame, FrameError, FrameType, cobs_decode, cobs_encode, crc16_ccitt_false,
    decode_frame, encode_frame, split_frames, split_stream,
)
from node.sampler import VARIABLES, encode_batch, encode_sample


def test_cobs_round_trip_zero_in_every_position():
    for pos in range(6):
        data = bytearray(b"ABCDEF")
        data[pos] = 0
        data = bytes(data)
        assert cobs_decode(cobs_encode(data)) == data


def test_cobs_round_trip_all_zeros():
    data = b"\x00" * 10
    assert cobs_decode(cobs_encode(data)) == data


def test_cobs_round_trip_empty():
    assert cobs_decode(cobs_encode(b"")) == b""


def test_cobs_round_trip_block_over_254_bytes():
    data = bytes(range(256)) * 2  # fuerza el caso código=0xFF (bloque de 254 sin cero)
    assert cobs_decode(cobs_encode(data)) == data


def test_crc16_ccitt_false_known_vector():
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_frame_round_trip_payload_with_zero_bytes():
    payload = bytes([0, 1, 0, 2, 0, 3, 0])
    raw = encode_frame(1, FrameType.DATA, 10, 84213, 3, payload)
    frame = decode_frame(raw)
    assert frame == Frame(1, FrameType.DATA, 10, 84213, 3, payload)


def test_frame_tolerates_trailing_rssi_byte():
    raw = encode_frame(1, FrameType.DATA, 10, 1, 1, b"x")
    frame = decode_frame(raw + bytes([142]))
    assert frame.payload == b"x"


def test_corrupted_frame_rejected():
    raw = bytearray(encode_frame(1, FrameType.DATA, 10, 1, 1, b"hola"))
    raw[3] ^= 0xFF
    with pytest.raises(FrameError):
        decode_frame(bytes(raw))


def test_truncated_frame_raises_controlled_error():
    raw = encode_frame(1, FrameType.DATA, 10, 1, 1, b"hola")
    with pytest.raises(FrameError):
        decode_frame(raw[:-3])


def test_empty_input_raises_controlled_error():
    with pytest.raises(FrameError):
        decode_frame(b"")


# Sufijo literal, NO RSSI_SUFFIX_LEN: esto es lo que el hardware hace, no lo que el código cree
# que hace. Si se derivara de la constante, el test seguiría en verde tras bajarla a 1 -- que es
# exactamente el fallo que dejó pasar la suite mientras el enlace real no levantaba.
_SUFIJO_RSSI_HW = bytes([0x33, 0x34])  # medido con tools.hello_test, módulos pegados


def _con_sufijo_rssi(*frames: bytes) -> bytes:
    """Flujo tal y como sale del dongle: cada paquete seguido de sus bytes de RSSI."""
    return b"".join(f + _SUFIJO_RSSI_HW for f in frames)


def test_stream_de_varias_tramas_no_se_desalinea_con_el_sufijo_rssi():
    """Regresión del fallo de enlace real: con el sufijo mal contado la PRIMERA trama decodifica
    y todas las siguientes mueren como 'bloque COBS truncado', porque el byte sobrante queda
    pegado al frente de la trama siguiente y nunca se resincroniza."""
    enviadas = [encode_frame(1, FrameType.DATA, 21, seq, 1, b"xy") for seq in (1, 2, 3)]
    frames, resto = split_stream(_con_sufijo_rssi(*enviadas), rssi_append=True)

    assert resto == b""
    assert [decode_frame(raw).seq_inicial for raw, _ in frames] == [1, 2, 3]
    assert [rssi for _, rssi in frames] == [-0x33 / 2] * 3


def test_stream_espera_al_sufijo_rssi_completo():
    """El sufijo puede llegar partido entre dos lecturas del puerto: la trama no se puede
    entregar hasta tenerlo entero, o el byte que falta se cuela en la trama siguiente."""
    completa = _con_sufijo_rssi(encode_frame(1, FrameType.DATA, 21, 1, 1, b"xy"))
    for corte in range(1, RSSI_SUFFIX_LEN + 1):
        frames, resto = split_stream(completa[:-corte], rssi_append=True)
        assert frames == []
        assert resto == completa[:-corte]


def test_concatenated_frames_split_cleanly():
    a = encode_frame(1, FrameType.DATA, 10, 1, 1, b"aa")
    b = encode_frame(1, FrameType.DATA, 10, 2, 1, b"bb")
    complete, rest = split_frames(a + b)
    assert rest == b""
    assert [decode_frame(f).payload for f in complete] == [b"aa", b"bb"]


def test_incomplete_trailing_frame_kept_as_remainder():
    a = encode_frame(1, FrameType.DATA, 10, 1, 1, b"aa")
    partial = encode_frame(1, FrameType.DATA, 10, 2, 1, b"bb")[:-2]
    complete, rest = split_frames(a + partial)
    assert len(complete) == 1
    assert rest == partial


def _lote(n_muestras: int) -> bytes:
    muestras = [(float(i), encode_sample({v: 1.0 for v in VARIABLES})) for i in range(n_muestras)]
    return encode_batch(0.0, muestras)


def test_encode_frame_rejects_frame_over_the_240b_module_limit():
    """El módulo LoRa (firmware DTU) descarta o corrompe sin avisar cualquier paquete de más de
    240 B (PLAN.md Fase 3, "Límite del módulo"). batch_size=13 con las 4 variables por defecto ya
    lo supera; nada en encode_frame()/node/main.py lo detectaba antes de esto, así que la trama
    salía igual, truncada, sin ningún error en el código."""
    payload = _lote(13)
    with pytest.raises(FrameError, match="240"):
        encode_frame(1, FrameType.DATA, 10, 1, 13, payload)


def test_encode_frame_allows_the_default_batch_size():
    payload = _lote(10)  # batch_size=10 por defecto, 4 variables: el caso normal, con margen
    raw = encode_frame(1, FrameType.DATA, 10, 1, 10, payload)
    assert len(raw) <= 240
