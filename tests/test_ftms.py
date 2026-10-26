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

"""FTMS and Heart Rate wire-format tests."""

import struct

import pytest

from rullo.devices import ftms


def test_indoor_bike_data_with_speed_cadence_power_hr() -> None:
    flags = (1 << 2) | (1 << 6) | (1 << 9)  # speed present (bit 0 clear)
    data = struct.pack("<HHHhB", flags, 3240, 180, 250, 142)
    bike = ftms.parse_indoor_bike_data(data)
    assert bike.speed == pytest.approx(32.4 / 3.6)
    assert (bike.cadence, bike.power, bike.heart_rate) == (90, 250, 142)


def test_indoor_bike_data_skips_fields_we_do_not_use() -> None:
    # More Data set (no speed); average speed, total distance and resistance
    # come before power and must be skipped correctly.
    flags = 1 | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 6)
    data = struct.pack("<HH", flags, 3000) + (1234).to_bytes(3, "little")
    data += struct.pack("<hh", 20, -5)  # resistance, then a negative power
    bike = ftms.parse_indoor_bike_data(data)
    assert bike.speed is None and bike.cadence is None
    assert bike.power == 0  # negative readings are clamped


def test_features() -> None:
    machine = (1 << 1) | (1 << 14)
    target = (1 << 3) | (1 << 13)
    f = ftms.parse_features(struct.pack("<II", machine, target))
    assert f.cadence and f.power and f.target_power and f.simulation
    assert not f.heart_rate
    assert not ftms.parse_features(struct.pack("<II", 0, 1 << 3)).simulation


def test_control_point_encoding() -> None:
    assert ftms.request_control() == b"\x00"
    assert ftms.set_target_power(250) == bytes([0x05, 0xFA, 0x00])
    sim = ftms.set_simulation(4.2)
    assert sim[0] == 0x11
    assert struct.unpack_from("<h", sim, 3)[0] == 420  # hundredths of a percent
    assert struct.unpack_from("<h", ftms.set_simulation(-3.5), 3)[0] == -350
    assert ftms.parse_response(bytes([0x80, 0x05, 0x01])) == (0x05, 0x01)


def test_heart_rate_formats() -> None:
    assert ftms.parse_heart_rate(bytes([0x00, 72])) == 72
    assert ftms.parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300
