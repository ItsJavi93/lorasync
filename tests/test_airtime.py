from common.airtime import frame_time_s, slot_duration_s, channel_occupancy


def test_frame_time_194b_sf7_bw500k():
    t = frame_time_s(payload_len=194, sf=7, bw_hz=500_000, cr=1, crc=True)
    assert abs(t * 1000 - 76.9) < 0.2


def test_slot_duration_adds_guard():
    t = frame_time_s(194, 7, 500_000, 1, True)
    slot = slot_duration_s(t, guard_s=0.060)
    assert abs(slot * 1000 - 136.9) < 0.2


def test_channel_occupancy_30_nodes():
    t = frame_time_s(194, 7, 500_000, 1, True)
    slot = slot_duration_s(t)
    occ = channel_occupancy(30, slot, superframe_s=10)
    assert abs(occ - 0.415) < 0.01
