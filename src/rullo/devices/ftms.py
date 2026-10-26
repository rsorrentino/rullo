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

"""Bluetooth Fitness Machine Service (FTMS) and Heart Rate wire formats.

Pure encode/decode functions, so the protocol is tested without hardware.
"""

import struct
from dataclasses import dataclass


def uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


FTMS_SERVICE = uuid16(0x1826)
FTMS_FEATURE = uuid16(0x2ACC)
INDOOR_BIKE_DATA = uuid16(0x2AD2)
CONTROL_POINT = uuid16(0x2AD9)
HR_SERVICE = uuid16(0x180D)
HR_MEASUREMENT = uuid16(0x2A37)

OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_TARGET_POWER = 0x05
OP_START_OR_RESUME = 0x07
OP_SET_SIM_PARAMS = 0x11
OP_RESPONSE = 0x80
RESULT_SUCCESS = 0x01


@dataclass(frozen=True)
class Features:
    """The subset of the FTMS feature bitfields the app cares about."""

    cadence: bool
    power: bool
    heart_rate: bool
    target_power: bool
    simulation: bool


def parse_features(data: bytes) -> Features:
    machine, target = struct.unpack_from("<II", data)
    return Features(
        cadence=bool(machine & (1 << 1)),
        heart_rate=bool(machine & (1 << 10)),
        power=bool(machine & (1 << 14)),
        target_power=bool(target & (1 << 3)),
        simulation=bool(target & (1 << 13)),
    )


@dataclass(frozen=True)
class BikeData:
    speed: float | None = None  # m/s
    cadence: int | None = None  # rpm
    power: int | None = None  # W
    heart_rate: int | None = None  # bpm


# Indoor Bike Data optional fields in wire order: (flag bit, size in bytes).
# Bit 0 is inverted: instantaneous speed is present when it is CLEAR.
_BIKE_FIELDS = (
    (1, 2),  # average speed
    (2, 2),  # instantaneous cadence, 0.5 rpm
    (3, 2),  # average cadence
    (4, 3),  # total distance
    (5, 2),  # resistance level
    (6, 2),  # instantaneous power, sint16 W
    (7, 2),  # average power
    (8, 5),  # expended energy (total, per hour, per minute)
    (9, 1),  # heart rate
)


def parse_indoor_bike_data(data: bytes) -> BikeData:
    (flags,) = struct.unpack_from("<H", data)
    offset = 2
    speed = cadence = power = hr = None
    if not flags & 1:
        speed = struct.unpack_from("<H", data, offset)[0] / 100.0 / 3.6
        offset += 2
    for bit, size in _BIKE_FIELDS:
        if not flags & (1 << bit):
            continue
        if bit == 2:
            cadence = round(struct.unpack_from("<H", data, offset)[0] / 2)
        elif bit == 6:
            power = max(struct.unpack_from("<h", data, offset)[0], 0)
        elif bit == 9:
            hr = data[offset] or None
        offset += size
    return BikeData(speed=speed, cadence=cadence, power=power, heart_rate=hr)


def parse_heart_rate(data: bytes) -> int | None:
    if data[0] & 1:
        value = int(struct.unpack_from("<H", data, 1)[0])
    else:
        value = data[1]
    return value or None


def request_control() -> bytes:
    return bytes([OP_REQUEST_CONTROL])


def start_or_resume() -> bytes:
    return bytes([OP_START_OR_RESUME])


def set_target_power(watts: int) -> bytes:
    return struct.pack("<Bh", OP_SET_TARGET_POWER, max(-32768, min(32767, watts)))


def set_simulation(
    grade_percent: float, crr: float = 0.004, cw: float = 0.51, wind_mps: float = 0.0
) -> bytes:
    """Set Indoor Bike Simulation Parameters; grade in hundredths of a percent."""
    grade = max(-32768, min(32767, round(grade_percent * 100)))
    return struct.pack(
        "<BhhBB",
        OP_SET_SIM_PARAMS,
        round(wind_mps * 1000),
        grade,
        max(0, min(255, round(crr * 10000))),
        max(0, min(255, round(cw * 100))),
    )


def parse_response(data: bytes) -> tuple[int, int] | None:
    """(request opcode, result code) for a control point indication."""
    if len(data) >= 3 and data[0] == OP_RESPONSE:
        return data[1], data[2]
    return None
