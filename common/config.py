"""Configuración del sistema: dataclass + carga desde TOML, con validación de banda legal."""
from dataclasses import dataclass
import tomllib

CHANNEL_MIN = 65
CHANNEL_MAX = 78
CHANNEL_BASE_MHZ = 850


class InvalidChannelError(ValueError):
    pass


@dataclass
class RadioConfig:
    port: str
    addr: int
    channel: int
    sf: int = 7
    bw_code: int = 2  # 0=125k,1=250k,2=500k
    cr: int = 1
    power_dbm: int = 10
    netid: int = 0
    lbt: bool = False
    rssi_append: bool = True
    allow_out_of_band: bool = False

    def __post_init__(self):
        if not (CHANNEL_MIN <= self.channel <= CHANNEL_MAX) and not self.allow_out_of_band:
            freq = CHANNEL_BASE_MHZ + self.channel
            raise InvalidChannelError(
                f"Canal {self.channel} ({freq} MHz) fuera de la banda libre colombiana "
                f"915-928 MHz (canales {CHANNEL_MIN}-{CHANNEL_MAX}). "
                f"868 MHz (canal 18, valor de fábrica) NO es legal en Colombia: cae en la banda "
                f"restringida 851-915 MHz (ANE). Usa 'allow_out_of_band = true' para forzar."
            )

    @property
    def freq_mhz(self) -> int:
        return CHANNEL_BASE_MHZ + self.channel


def load_config(path: str) -> RadioConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    section = data.get("radio", data)
    return RadioConfig(**section)
