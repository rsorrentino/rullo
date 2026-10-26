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

"""Cycling Power, Cycling Speed and Cadence, and Wahoo trainer control formats.

Wahoo KICKR trainers without FTMS take commands on a vendor characteristic
inside the Cycling Power service. The opcodes are not published by Wahoo; they
follow the open-source implementations (GoldenCheetah, qdomyos-zwift).
Pure encode/decode functions, so they are tested without hardware.
"""

import struct
from dataclasses import dataclass

from rullo.devices.ftms import uuid16

CYCLING_POWER_SERVICE = uuid16(0x1818)
CYCLING_POWER_MEASUREMENT = uuid16(0x2A63)
CSC_SERVICE = uuid16(0x1816)
CSC_MEASUREMENT = uuid16(0x2A5B)
WAHOO_CONTROL = "a026e005-0a7d-4ab3-97fa-f1500f9feb8b"

OP_UNLOCK = 0x20
OP_ERG = 0x42
OP_SIM = 0x43
OP_GRADE = 0x46


@dataclass(frozen=True)
class Crank:
    """Cumulative crank revolutions and the time of the last one (1/1024 s)."""

    revolutions: int  # uint16, wraps
    event_time: int  # uint16 in 1/1024 s, wraps


@dataclass(frozen=True)
class PowerData:
    power: int
    crank: Crank | None = None


# Cycling Power Measurement optional fields before crank data: (flag bit, size).
_CP_BEFORE_CRANK = ((0, 1), (2, 2), (4, 6))  # balance, torque, wheel revs + time


def parse_cycling_power(data: bytes) -> PowerData:
    flags, power = struct.unpack_from("<Hh", data)
    offset = 4
    for bit, size in _CP_BEFORE_CRANK:
        if flags & (1 << bit):
            offset += size
    crank = None
    if flags & (1 << 5):
        crank = Crank(*struct.unpack_from("<HH", data, offset))
    return PowerData(max(power, 0), crank)


def parse_csc(data: bytes) -> Crank | None:
    """Crank data from a Cycling Speed and Cadence measurement, if present."""
    flags = data[0]
    offset = 1 + (6 if flags & 1 else 0)  # skip wheel revolutions + time
    if not flags & 2:
        return None
    return Crank(*struct.unpack_from("<HH", data, offset))


class CadenceMeter:
    """Turns successive crank events into rpm; reports 0 once pedalling stops."""

    def __init__(self, stale_s: float = 3.0) -> None:
        self._stale_s = stale_s
        self._last: Crank | None = None
        self._last_change = 0.0
        self.rpm: int | None = None

    def update(self, crank: Crank, now: float) -> None:
        prev, self._last = self._last, crank
        if prev is None:
            self._last_change = now
            return
        revs = (crank.revolutions - prev.revolutions) & 0xFFFF
        ticks = (crank.event_time - prev.event_time) & 0xFFFF
        if revs and ticks:
            self.rpm = round(revs * 60 * 1024 / ticks)
            self._last_change = now
        elif now - self._last_change > self._stale_s:
            self.rpm = 0

    def current(self, now: float) -> int | None:
        if self.rpm and now - self._last_change > self._stale_s:
            self.rpm = 0
        return self.rpm


def unlock() -> bytes:
    return bytes([OP_UNLOCK, 0xEE, 0xFC])


def erg(watts: int) -> bytes:
    return struct.pack("<BH", OP_ERG, max(0, min(0xFFFF, watts)))


def sim_mode(mass_kg: float, crr: float = 0.004, cw: float = 0.51) -> bytes:
    """Enter simulation mode with rider weight, rolling and wind resistance."""

    def clamp(v: float) -> int:
        return max(0, min(0xFFFF, round(v)))

    return struct.pack(
        "<BHHH", OP_SIM, clamp(mass_kg * 100), clamp(crr * 10000), clamp(cw * 1000)
    )


def grade(percent: float) -> bytes:
    """Grade as a fraction in [-1, 1] mapped onto 0..65535."""
    fraction = max(-1.0, min(1.0, percent / 100.0))
    return struct.pack("<BH", OP_GRADE, round((fraction + 1.0) * 65535 / 2.0))
