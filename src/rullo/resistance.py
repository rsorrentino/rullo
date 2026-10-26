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

"""Resistance controller: the only writer of trainer commands.

SIM mode sends the course grade scaled by the difficulty setting; ERG mode sends
the workout target. Writes are rate-limited (trainers choke on floods), ERG
targets ramp at a bounded rate, and a cadence floor releases ERG resistance so a
tiring rider does not fall into the "spiral of death".
"""

import logging
from dataclasses import dataclass

from rullo.devices.base import Device, Mode
from rullo.workout import Workout

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResistanceSettings:
    difficulty: float = 0.5  # fraction of the real grade the trainer applies
    min_interval_s: float = 1.0  # at most one write per second per command
    grade_step: float = 0.1  # % change worth sending
    max_ramp_w_per_s: float = 50.0
    cadence_floor: int = 55  # rpm; below it ERG is released
    release_fraction: float = 0.5  # ERG target fraction while released
    min_grade: float = -10.0
    max_grade: float = 20.0


class ResistanceController:
    """Decides, every tick, what the trainer should feel and writes it."""

    def __init__(
        self,
        device: Device,
        mode: Mode,
        ftp: int,
        workout: Workout | None = None,
        target_power: int | None = None,
        settings: ResistanceSettings | None = None,
    ) -> None:
        if mode is Mode.ERG and workout is None and target_power is None:
            raise ValueError("ERG mode needs a workout or a target power")
        self._device = device
        self.mode = mode
        self._ftp = ftp
        self._workout = workout
        self._fixed_target = target_power
        self._s = settings or ResistanceSettings()
        self._last_grade: float | None = None
        self._last_grade_t = -1e9
        self._sent_target: float | None = None
        self._last_target_t = -1e9
        self.released = False
        self.target: int | None = None  # what the rider should hold right now

    def planned_target(self, t: float) -> int | None:
        if self._workout is not None:
            return self._workout.target_at(t, self._ftp)
        return self._fixed_target

    def trainer_grade(self, grade: float) -> float:
        scaled = grade * self._s.difficulty
        return max(self._s.min_grade, min(self._s.max_grade, round(scaled, 2)))

    async def update(self, t: float, grade: float, cadence: int | None) -> None:
        if self.mode is Mode.SIM:
            await self._update_grade(t, grade)
        elif self.mode is Mode.ERG:
            await self._update_erg(t, grade, cadence)

    async def _update_grade(self, t: float, grade: float) -> None:
        wanted = self.trainer_grade(grade)
        if t - self._last_grade_t < self._s.min_interval_s:
            return
        if (
            self._last_grade is not None
            and abs(wanted - self._last_grade) < self._s.grade_step
        ):
            return
        await self._device.set_grade(wanted)
        self._last_grade, self._last_grade_t = wanted, t

    async def _update_erg(self, t: float, grade: float, cadence: int | None) -> None:
        planned = self.planned_target(t)
        self.target = planned
        if planned is None:  # free-ride block: let the course drive resistance
            self._sent_target = None
            await self._update_grade(t, grade)
            return
        if cadence is not None:
            if cadence < self._s.cadence_floor and not self.released:
                self.released = True
                logger.info("cadence %s rpm below floor: releasing ERG", cadence)
            elif cadence >= self._s.cadence_floor + 5 and self.released:
                self.released = False
        goal = planned * self._s.release_fraction if self.released else float(planned)
        if t - self._last_target_t < self._s.min_interval_s:
            return
        if self._sent_target is None or self.released:
            command = goal  # first write, or a release: jump straight there
        else:
            since = min(t - self._last_target_t, self._s.min_interval_s)
            step = self._s.max_ramp_w_per_s * since
            command = max(self._sent_target - step, min(self._sent_target + step, goal))
        watts = round(command)
        if self._sent_target is not None and watts == round(self._sent_target):
            return
        await self._device.set_target_power(watts)
        self._sent_target, self._last_target_t = float(watts), t
