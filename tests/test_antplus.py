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

"""ANT+ page codec tests."""

import pytest

from rullo.devices import antplus
from rullo.devices.wahoo import Crank


def test_fec_trainer_page() -> None:
    # Page 0x19: event, cadence 92, accumulated power, power 0x0FA = 250 W.
    page = bytes([0x19, 7, 92, 0x10, 0x27, 0xFA, 0x30, 0x00])
    assert antplus.parse_fec(page) == antplus.Reading(250, 92)
    assert (
        antplus.parse_fec(bytes([0x19, 7, 0xFF, 0, 0, 0xFF, 0x0F, 0]))
        == antplus.Reading()
    )
    assert antplus.parse_fec(bytes([0x10] + [0] * 7)) is None  # general FE page


def test_power_meter_page() -> None:
    page = bytes([0x10, 3, 0xFF, 88, 0, 0, 0x2C, 0x01])
    assert antplus.parse_power_meter(page) == antplus.Reading(300, 88)
    assert antplus.parse_power_meter(bytes([0x12] + [0] * 7)) is None


def test_heart_rate_and_cadence_pages() -> None:
    assert antplus.parse_heart_rate(bytes([0x04, 0, 0, 0, 0, 0, 0, 152])) == 152
    assert antplus.parse_heart_rate(bytes(8)) is None
    combined = bytes([0x00, 0x04, 0x2A, 0x00, 0, 0, 0, 0])  # time 1024, revs 42
    assert antplus.parse_cadence(antplus.SPEED_CADENCE, combined) == Crank(42, 1024)
    cadence = bytes([0x80, 0, 0, 0, 0x00, 0x08, 0x07, 0x00])  # time 2048, revs 7
    assert antplus.parse_cadence(antplus.CADENCE, cadence) == Crank(7, 2048)


def test_control_pages() -> None:
    assert antplus.target_power(250) == bytes(
        [0x31, 255, 255, 255, 255, 255, 0xE8, 0x03]
    )
    track = antplus.track_resistance(4.2)
    assert track[0] == 0x33
    assert track[5] | (track[6] << 8) == 20420  # (4.2 + 200) / 0.01
    assert track[7] == 80  # Crr 0.004 in 5e-5 units
    assert antplus.track_resistance(-5.0)[5:7] == (19500).to_bytes(2, "little")
    user = antplus.user_configuration(78.0, bike_kg=9.0)
    assert user[0] == 0x37 and user[1] | (user[2] << 8) == 6900  # 69 kg rider
    assert ((user[4] >> 4) | (user[5] << 4)) == 180  # 9 kg bike in 0.05 kg


@pytest.mark.parametrize("watts", [-10, 5000])
def test_target_power_is_clamped(watts: int) -> None:
    raw = antplus.target_power(watts)
    assert 0 <= raw[6] | (raw[7] << 8) <= 16000
