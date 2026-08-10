import pytest

from common.frame import (
    Frame, FrameError, FrameType, cobs_decode, cobs_encode, crc16_ccitt_false,
    decode_frame, encode_frame, split_frames,
)


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
