"""Cálculo de tiempo de aire LoRa, duración de slot y capacidad de canal."""
import math

PREAMBLE_SYMBOLS = 8


def symbol_time_s(sf: int, bw_hz: float) -> float:
    return (2 ** sf) / bw_hz


def preamble_time_s(sf: int, bw_hz: float) -> float:
    return (PREAMBLE_SYMBOLS + 4.25) * symbol_time_s(sf, bw_hz)


def payload_symbols(payload_len: int, sf: int, cr: int, crc: bool = True, low_dr_opt: bool = False) -> int:
    """n_payload de la fórmula estándar del datasheet SX1262 (explicit header)."""
    de = 1 if low_dr_opt else 0
    crc_bits = 16 if crc else 0
    numerator = 8 * payload_len - 4 * sf + 28 + crc_bits - 20 * 0  # header presente (explicit) -> 0 extra
    num = numerator
    ceil_term = math.ceil(num / (4 * (sf - 2 * de))) if num > 0 else 0
    return 8 + max(ceil_term * (cr + 4), 0)


def frame_time_s(payload_len: int, sf: int, bw_hz: float, cr: int, crc: bool = True) -> float:
    tsym = symbol_time_s(sf, bw_hz)
    t_preamble = preamble_time_s(sf, bw_hz)
    n_payload = payload_symbols(payload_len, sf, cr, crc=crc)
    return t_preamble + n_payload * tsym


def slot_duration_s(frame_time_s_: float, guard_s: float = 0.060) -> float:
    return frame_time_s_ + guard_s


def channel_occupancy(num_nodes: int, slot_s: float, superframe_s: float) -> float:
    return (num_nodes * slot_s) / superframe_s
