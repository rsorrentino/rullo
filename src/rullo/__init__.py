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

"""Rullo: headless core of an offline indoor-cycling training app."""

from rullo.bus import TelemetryBus
from rullo.devices.base import Device, Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.loop import SessionState, run, run_loop
from rullo.physics import RiderParams, speed_from_power
from rullo.telemetry import Telemetry

__all__ = [
    "Device",
    "Mode",
    "RiderParams",
    "SessionState",
    "SimulatedDevice",
    "Telemetry",
    "TelemetryBus",
    "run",
    "run_loop",
    "speed_from_power",
]
