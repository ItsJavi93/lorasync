"""Fuente de datos del nodo. Stub reemplazable: genera datos sintéticos hasta que se conecten
sensores reales -- aquí es donde se cablean después, sin tocar node/main.py.

También codifica el lote que viaja dentro de una trama DATA: cada muestra ocupa
ts_diff_ms(2B) + valores(4 x float32 = 16B) = 18B, y el lote lleva una única marca de tiempo
base (8B, float64) de la que se derivan los ts_diff_ms de cada muestra (ver PLAN.md Fase 2,
nota 2: el tamaño real de trama se recalcula aquí, no se asume el PL=194B de la Fase 0/1)."""
import random
import struct

VARIABLES = ("temp_c", "volt", "hum_pct", "press_hpa")
_SAMPLE_FMT = ">" + "f" * len(VARIABLES)
SAMPLE_PAYLOAD_LEN = struct.calcsize(_SAMPLE_FMT)  # 16B

_BATCH_HEADER_FMT = ">d"
_BATCH_HEADER_LEN = struct.calcsize(_BATCH_HEADER_FMT)  # 8B
_ITEM_TS_FMT = ">H"
_ITEM_TS_LEN = struct.calcsize(_ITEM_TS_FMT)  # 2B


def read_sample() -> dict[str, float]:
    """Stub: valores sintéticos plausibles. Sustituir por lectura real de sensores."""
    return {
        "temp_c": round(random.uniform(20.0, 30.0), 2),
        "volt": round(random.uniform(11.5, 12.6), 2),
        "hum_pct": round(random.uniform(30.0, 70.0), 1),
        "press_hpa": round(random.uniform(1000.0, 1025.0), 1),
    }


def encode_sample(values: dict[str, float]) -> bytes:
    return struct.pack(_SAMPLE_FMT, *(values[v] for v in VARIABLES))


def decode_sample(data: bytes) -> dict[str, float]:
    return dict(zip(VARIABLES, struct.unpack(_SAMPLE_FMT, data)))


def encode_batch(ts_base: float, samples: list[tuple[float, bytes]]) -> bytes:
    """samples: [(ts_utc, payload_16B), ...] en orden ascendente de ts_utc (garantizado por
    NodeStore.get_pending, que devuelve en orden de seq). ts_base debe ser <= todos los ts_utc."""
    out = bytearray(struct.pack(_BATCH_HEADER_FMT, ts_base))
    for ts_utc, payload in samples:
        diff_ms = round((ts_utc - ts_base) * 1000)
        out += struct.pack(_ITEM_TS_FMT, diff_ms)
        out += payload
    return bytes(out)


def decode_batch(data: bytes, n_muestras: int) -> list[tuple[float, dict[str, float]]]:
    ts_base = struct.unpack_from(_BATCH_HEADER_FMT, data, 0)[0]
    stride = _ITEM_TS_LEN + SAMPLE_PAYLOAD_LEN
    result = []
    offset = _BATCH_HEADER_LEN
    for _ in range(n_muestras):
        diff_ms = struct.unpack_from(_ITEM_TS_FMT, data, offset)[0]
        values = decode_sample(data[offset + _ITEM_TS_LEN:offset + stride])
        result.append((ts_base + diff_ms / 1000, values))
        offset += stride
    return result
