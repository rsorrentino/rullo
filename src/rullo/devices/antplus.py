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

"""ANT+ data pages: FE-C trainers, heart rate, bike power and cadence sensors.

Pure encode/decode of 8-byte ANT+ pages, following the ANT+ device profiles,
so the protocol is tested without a USB stick.
"""

from dataclasses import dataclass

from rullo.devices.wahoo import Crank

# ANT+ device types and channel periods (in 1/32768 s).
FE_C = 17
BIKE_POWER = 11
HEART_RATE = 120
SPEED_CADENCE = 121
CADENCE = 122
PERIODS = {
    FE_C: 8192,
    BIKE_POWER: 8182,
    HEART_RATE: 8070,
    SPEED_CADENCE: 8086,
    CADENCE: 8102,
}
KINDS = {
    FE_C: ("trainer", "ANT+ FE-C trainer"),
    BIKE_POWER: ("trainer", "ANT+ power meter (no resistance control)"),
    HEART_RATE: ("heart_rate", "ANT+ heart-rate strap"),
    SPEED_CADENCE: ("cadence", "ANT+ speed/cadence sensor"),
    CADENCE: ("cadence", "ANT+ cadence sensor"),
}

PAGE_GENERAL_FE = 0x10
PAGE_TRAINER = 0x19
PAGE_TARGET_POWER = 0x31
PAGE_TRACK_RESISTANCE = 0x33
PAGE_USER_CONFIG = 0x37
PAGE_POWER_ONLY = 0x10


@dataclass(frozen=True)
class Reading:
    power: int | None = None
    cadence: int | None = None


def parse_fec(page: bytes) -> Reading | None:
    """Power and cadence from the FE-C specific trainer page (0x19)."""
    if page[0] != PAGE_TRAINER:
        return None
    cadence = None if page[2] == 0xFF else page[2]
    power = page[5] | ((page[6] & 0x0F) << 8)
    return Reading(None if power == 0xFFF else power, cadence)


def parse_power_meter(page: bytes) -> Reading | None:
    """Power and cadence from the bike power "power only" page (0x10)."""
    if page[0] != PAGE_POWER_ONLY:
        return None
    cadence = None if page[3] == 0xFF else page[3]
    return Reading(page[6] | (page[7] << 8), cadence)


def parse_heart_rate(page: bytes) -> int | None:
    """Computed heart rate, byte 7 of every heart-rate page."""
    return page[7] or None


def parse_cadence(device_type: int, page: bytes) -> Crank:
    """Crank event time (1/1024 s) and revolution count from a cadence page."""
    if device_type == SPEED_CADENCE:  # cadence fields come first
        return Crank(page[2] | (page[3] << 8), page[0] | (page[1] << 8))
    return Crank(page[6] | (page[7] << 8), page[4] | (page[5] << 8))


def target_power(watts: int) -> bytes:
    """FE-C target power page, in 0.25 W units."""
    raw = max(0, min(4000, watts)) * 4
    return bytes(
        [PAGE_TARGET_POWER, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, raw & 0xFF, raw >> 8]
    )


def track_resistance(grade_percent: float, crr: float = 0.004) -> bytes:
    """FE-C track resistance page: grade in 0.01 % offset by -200 %, Crr in 5e-5."""
    grade = round((max(-200.0, min(200.0, grade_percent)) + 200.0) * 100)
    rolling = max(0, min(254, round(crr / 5e-5)))
    return bytes(
        [
            PAGE_TRACK_RESISTANCE,
            0xFF,
            0xFF,
            0xFF,
            0xFF,
            grade & 0xFF,
            grade >> 8,
            rolling,
        ]
    )


def user_configuration(mass_kg: float, bike_kg: float = 9.0) -> bytes:
    """FE-C user configuration: rider weight (0.01 kg) and bike weight (0.05 kg)."""
    user = max(0, min(0xFFFE, round((mass_kg - bike_kg) * 100)))
    bike = max(0, min(0xFFE, round(bike_kg / 0.05)))
    return bytes(
        [
            PAGE_USER_CONFIG,
            user & 0xFF,
            user >> 8,
            0xFF,
            0x0F | ((bike & 0x0F) << 4),
            bike >> 4,
            0xFF,
            0x00,
        ]
    )


# Tacx FE-C over Bluetooth: the same FE-C pages, wrapped in ANT message frames
# (sync 0xA4, length 9, message id, channel 5, 8-byte page, XOR checksum) on a
# vendor service.
FEC_BLE_SERVICE = "6e40fec1-b5a3-f393-e0a9-e50e24dcca9e"
FEC_BLE_NOTIFY = "6e40fec2-b5a3-f393-e0a9-e50e24dcca9e"  # trainer -> app
FEC_BLE_WRITE = "6e40fec3-b5a3-f393-e0a9-e50e24dcca9e"  # app -> trainer
_SYNC, _BROADCAST, _ACKNOWLEDGED, _FEC_CHANNEL = 0xA4, 0x4E, 0x4F, 0x05


def ble_frame(page: bytes) -> bytes:
    """Wrap an 8-byte FE-C page as an acknowledged ANT message for BLE."""
    if len(page) != 8:
        raise ValueError("an ANT+ page is 8 bytes")
    body = bytes([_SYNC, 9, _ACKNOWLEDGED, _FEC_CHANNEL]) + page
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum])


def ble_page(frame: bytes) -> bytes | None:
    """The 8-byte page inside an ANT message from a Tacx trainer, if valid."""
    if (
        len(frame) < 13
        or frame[0] != _SYNC
        or frame[2] not in (_BROADCAST, _ACKNOWLEDGED)
    ):
        return None
    checksum = 0
    for byte in frame[: 4 + 8]:
        checksum ^= byte
    if checksum != frame[12]:
        return None
    return bytes(frame[4:12])
