import pytest

from common.config import RadioConfig, InvalidChannelError


def make(**overrides):
    base = dict(port="COM3", addr=1, channel=70)
    base.update(overrides)
    return RadioConfig(**base)


def test_valid_channel_in_range():
    cfg = make(channel=70)
    assert cfg.freq_mhz == 920


def test_channel_out_of_range_rejected():
    with pytest.raises(InvalidChannelError):
        make(channel=18)  # 868 MHz, no legal en Colombia


def test_channel_below_range_rejected():
    with pytest.raises(InvalidChannelError):
        make(channel=64)


def test_channel_above_range_rejected():
    with pytest.raises(InvalidChannelError):
        make(channel=79)


def test_allow_out_of_band_bypasses_validation():
    cfg = make(channel=18, allow_out_of_band=True)
    assert cfg.channel == 18


def test_boundaries_are_valid():
    assert make(channel=65).channel == 65
    assert make(channel=78).channel == 78
