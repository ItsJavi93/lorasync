"""Un lote que cruza un hueco temporal (nodo reiniciado tras un corte) no debe desbordar el
campo ts_diff_ms de 2B. Regresión del struct.error visto en hardware el 2026-08-09."""
import pytest

from node.main import _next_batch
from node.sampler import (MAX_BATCH_SPAN_S, SAMPLE_PAYLOAD_LEN, decode_batch, encode_batch,
                          trim_to_span)
from node.store import Sample

_PAYLOAD = b"\x00" * SAMPLE_PAYLOAD_LEN


def _samples(offsets_s: list[float], t0: float = 1_000_000.0) -> list[Sample]:
    return [Sample(seq=i + 1, ts_utc=t0 + off, payload=_PAYLOAD)
            for i, off in enumerate(offsets_s)]


def test_lote_contiguo_no_se_recorta():
    batch = _samples([float(i) for i in range(10)])  # 1 Hz, 10 s
    assert trim_to_span(batch) == batch


def test_lote_que_cruza_un_hueco_se_corta_en_el_hueco():
    # 3 muestras viejas, corte de 2 h, 7 muestras nuevas: el borde cae en el hueco.
    batch = _samples([0.0, 1.0, 2.0] + [7200.0 + i for i in range(7)])
    trimmed = trim_to_span(batch)
    assert len(trimmed) == 3
    assert trimmed == batch[:3]


def test_el_lote_recortado_siempre_codifica_y_decodifica():
    batch = _next_batch(_samples([0.0, 1.0] + [90_000.0 + i for i in range(8)]), 10, False)
    payload = encode_batch(batch[0].ts_utc, [(s.ts_utc, s.payload) for s in batch])
    assert len(decode_batch(payload, len(batch))) == len(batch)


def _cola_tras_reinicio(nuevas: int) -> list[Sample]:
    """Como en hardware (2026-09-16): 79 muestras viejas (seq 313-391) de la ejecución anterior,
    un corte de 1 h y `nuevas` muestras recién tomadas a 1 Hz (seq 392 en adelante)."""
    viejas = [Sample(313 + i, 1_000_000.0 + i, _PAYLOAD) for i in range(79)]
    recientes = [Sample(392 + i, 1_003_600.0 + i, _PAYLOAD) for i in range(nuevas)]
    return viejas + recientes


def test_lote_mas_nuevo_lleva_las_muestras_recientes_aunque_haya_hueco():
    """El lote "más nuevo" del drenado intercalado debe llevar lo último medido. Recortado desde el
    principio, se quedaba con la cola vieja (384-391, luego 386-391...) y reenviaba muestras que el
    maestro ya tenía, mientras las recién tomadas no salían hasta vaciar todo el atraso."""
    batch = _next_batch(_cola_tras_reinicio(nuevas=2), 10, prefer_newest=True)
    assert [s.seq for s in batch] == [392, 393]

    batch = _next_batch(_cola_tras_reinicio(nuevas=15), 10, prefer_newest=True)
    assert [s.seq for s in batch] == list(range(397, 407))


def test_lote_mas_nuevo_recortado_codifica_y_decodifica():
    batch = _next_batch(_cola_tras_reinicio(nuevas=3), 10, prefer_newest=True)
    payload = encode_batch(batch[0].ts_utc, [(s.ts_utc, s.payload) for s in batch])
    assert len(decode_batch(payload, len(batch))) == len(batch)


def test_nunca_devuelve_lote_vacio():
    # Aunque la segunda muestra ya exceda el rango, la primera siempre viaja.
    batch = _samples([0.0, MAX_BATCH_SPAN_S + 10])
    assert len(trim_to_span(batch)) == 1


def test_encode_batch_rechaza_un_lote_sin_recortar_con_mensaje_claro():
    batch = _samples([0.0, 7200.0])
    with pytest.raises(ValueError, match="trim_to_span"):
        encode_batch(batch[0].ts_utc, [(s.ts_utc, s.payload) for s in batch])
