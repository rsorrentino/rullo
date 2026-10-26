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

"""Normalised telemetry sample shared by every device and consumer."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Telemetry:
    """One telemetry sample.

    ``t`` is seconds since the start of the stream; units: watts, rpm, bpm, m/s.
    """

    t: float
    power: float | None = None
    cadence: float | None = None
    heart_rate: float | None = None
    speed: float | None = None
