"""Planificador de acceso al canal. Fase 3: solo ALOHA. TDMA llega en Fase 4."""
import random
import time
from abc import ABC, abstractmethod


class Scheduler(ABC):
    @abstractmethod
    def wait_for_slot(self) -> None:
        """Bloquea hasta que sea el momento de transmitir."""


class AlohaScheduler(Scheduler):
    """Retardo aleatorio antes de cada transmisión, para reducir la probabilidad de colisión
    entre nodos. El propio módulo hace Listen-Before-Talk (AT+LBT=1); en modo ALOHA debe estar
    activado en la configuración de radio -- este planificador solo añade el jitter previo."""

    def __init__(self, min_delay_s: float = 0.0, max_delay_s: float = 2.0,
                 rng: random.Random | None = None):
        self._min = min_delay_s
        self._max = max_delay_s
        self._rng = rng or random.Random()

    def wait_for_slot(self) -> None:
        time.sleep(self._rng.uniform(self._min, self._max))
