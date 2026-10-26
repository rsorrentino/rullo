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

"""Cycling Power, CSC and Wahoo control wire-format tests."""

import struct

import pytest

from rullo.devices import wahoo


def test_kickr_snap_power_packet() -> None:
    # Captured from a KICKR SNAP (firmware 2.3.63) at rest: torque + wheel data.
    reading = wahoo.parse_cycling_power(bytes.fromhex("14000000000000000000c403"))
    assert reading.power == 0 and reading.crank is None
    flags = (1 << 2) | (1 << 4)
    packet = struct.pack("<HhHIH", flags, 245, 0, 1000, 512)
    assert wahoo.parse_cycling_power(packet).power == 245


def test_power_packet_with_crank_data() -> None:
    flags = (1 << 0) | (1 << 5)  # pedal balance, then crank data
    packet = struct.pack("<HhBHH", flags, 310, 50, 77, 2048)
    reading = wahoo.parse_cycling_power(packet)
    assert reading.power == 310
    assert reading.crank == wahoo.Crank(77, 2048)


def test_csc_crank_data() -> None:
    both = struct.pack("<BIHHH", 3, 500, 100, 42, 4096)
    assert wahoo.parse_csc(both) == wahoo.Crank(42, 4096)
    assert wahoo.parse_csc(struct.pack("<BIH", 1, 500, 100)) is None


def test_cadence_from_crank_events_with_wraparound() -> None:
    meter = wahoo.CadenceMeter(stale_s=3.0)
    meter.update(wahoo.Crank(65535, 65000), 0.0)
    assert meter.current(0.0) is None
    meter.update(wahoo.Crank(0, (65000 + 683) & 0xFFFF), 0.7)  # 1 rev in 0.667 s
    assert meter.current(0.7) == 90
    meter.update(wahoo.Crank(0, (65000 + 683) & 0xFFFF), 2.0)  # no new revolution
    assert meter.current(2.0) == 90
    assert meter.current(4.0) == 0  # stopped pedalling


def test_wahoo_commands() -> None:
    assert wahoo.unlock() == bytes([0x20, 0xEE, 0xFC])
    assert wahoo.erg(250) == bytes([0x42, 0xFA, 0x00])
    assert wahoo.sim_mode(78.0) == struct.pack("<BHHH", 0x43, 7800, 40, 510)
    assert wahoo.grade(0.0) == struct.pack("<BH", 0x46, 32768)
    value = struct.unpack_from("<H", wahoo.grade(5.0), 1)[0]
    assert value == pytest.approx(1.05 * 65535 / 2, abs=1)
    assert wahoo.grade(-150.0) == struct.pack("<BH", 0x46, 0)  # clamped
