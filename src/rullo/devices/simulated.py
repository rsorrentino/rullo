# Rullo - offline indoor-cycling training app.
# Copyright (C) 2026 The Rullo contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Simulated device: a fake rider/trainer, no hardware needed."""

import asyncio
import logging
import random
from collections.abc import AsyncIterator

from rullo.devices.base import Mode
from rullo.telemetry import Telemetry

logger = logging.getLogger(__name__)

# Fraction of the gap to the ERG target closed on each tick.
_ERG_CONVERGENCE_RATE = 0.35
# Power eased away per percent of positive grade in SIM mode, floor at 30%.
_SIM_GRADE_EASE_PER_PERCENT = 0.015
_SIM_GRADE_EASE_FLOOR = 0.3


class SimulatedDevice:
    """Implements ``Device``; yields Telemetry at ``rate_hz`` with no hardware.

    In SIM mode the rider holds ``rider_power`` but eases off slightly as the
    course grade steepens. In ERG mode the emitted power converges toward the
    last ``set_target_power``. FREE mode ignores grade entirely. Output is
    deterministic for a given ``seed``.
    """

    def __init__(
        self,
        mode: Mode = Mode.SIM,
        rider_power: int = 200,
        cadence: int = 90,
        heart_rate: int = 140,
        rate_hz: float = 4.0,
        seed: int = 0,
    ) -> None:
        self._mode = mode
        self._rider_power = rider_power
        self._cadence = cadence
        self._heart_rate = heart_rate
        self._rate_hz = rate_hz
        self._rng = random.Random(seed)
        self._connected = False
        self.grade = 0.0
        self.target_power: int | None = None
        self._erg_power = float(rider_power)

    async def connect(self) -> None:
        self._connected = True

    async def telemetry(self) -> AsyncIterator[Telemetry]:
        dt = 1.0 / self._rate_hz
        t = 0.0
        while self._connected:
            yield Telemetry(
                t=t,
                power=self._next_power(),
                cadence=self._cadence,
                heart_rate=self._heart_rate,
            )
            t += dt
            await asyncio.sleep(dt)

    def _next_power(self) -> int:
        jitter = self._rng.gauss(0.0, 2.0)
        if self._mode is Mode.ERG and self.target_power is not None:
            target = float(self.target_power)
            self._erg_power += (target - self._erg_power) * _ERG_CONVERGENCE_RATE
            return max(round(self._erg_power + jitter), 0)
        if self._mode is Mode.SIM:
            ease = max(
                _SIM_GRADE_EASE_FLOOR,
                1.0 - _SIM_GRADE_EASE_PER_PERCENT * max(self.grade, 0.0),
            )
            return max(round(self._rider_power * ease + jitter), 0)
        return max(round(self._rider_power + jitter), 0)

    async def set_grade(self, percent: float) -> None:
        self.grade = percent
        logger.info("set_grade(%s)", percent)

    async def set_target_power(self, watts: int) -> None:
        self.target_power = watts
        logger.info("set_target_power(%s)", watts)

    async def disconnect(self) -> None:
        self._connected = False
