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

"""Simulated device: generates or replays a power trace, no hardware needed."""

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Sequence

from rullo.telemetry import Telemetry

logger = logging.getLogger(__name__)


class SimulatedDevice:
    """Yields telemetry at ``rate_hz``; replays ``trace`` or a generated wave."""

    def __init__(
        self,
        trace: Sequence[float] | None = None,
        rate_hz: float = 4.0,
        base_power: float = 200.0,
        cadence: float = 90.0,
        heart_rate: float = 140.0,
    ) -> None:
        self._trace = list(trace) if trace is not None else None
        self._rate_hz = rate_hz
        self._base_power = base_power
        self._cadence = cadence
        self._heart_rate = heart_rate
        self._connected = False
        self.grade = 0.0
        self.target_power: float | None = None

    async def connect(self) -> None:
        self._connected = True

    async def telemetry(self) -> AsyncIterator[Telemetry]:
        dt = 1.0 / self._rate_hz
        i = 0
        while self._connected:
            t = i * dt
            yield Telemetry(
                t=t,
                power=self._power_at(i, t),
                cadence=self._cadence,
                heart_rate=self._heart_rate,
            )
            i += 1
            await asyncio.sleep(dt)

    def _power_at(self, i: int, t: float) -> float:
        if self._trace is not None:
            return self._trace[i % len(self._trace)] if self._trace else 0.0
        return self._base_power + 20.0 * math.sin(t / 5.0)

    async def set_grade(self, percent: float) -> None:
        self.grade = percent
        logger.info("set_grade(%s)", percent)

    async def set_target_power(self, watts: float) -> None:
        self.target_power = watts
        logger.info("set_target_power(%s)", watts)

    async def disconnect(self) -> None:
        self._connected = False
