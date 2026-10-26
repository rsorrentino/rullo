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

"""FTMS / Heart Rate wire-format tests and the BLE adapter against a fake client."""

import asyncio
import struct
from collections.abc import Callable
from typing import Any

import pytest

from rullo.devices import ftms
from rullo.devices.ble import FtmsBleDevice


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


class FakeClient:
    def __init__(self, features: bytes, fail_connect: int = 0) -> None:
        self.features = features
        self.fail_connect = fail_connect
        self.connected = False
        self.writes: list[bytes] = []
        self.callbacks: dict[str, Callable[[Any, bytearray], None]] = {}

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        if self.fail_connect:
            self.fail_connect -= 1
            raise OSError("trainer not found")
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def read_gatt_char(self, char: str) -> bytearray:
        assert char == ftms.FTMS_FEATURE
        return bytearray(self.features)

    async def write_gatt_char(self, char: str, data: bytes, response: bool) -> None:
        self.writes.append(bytes(data))

    async def start_notify(
        self, char: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        self.callbacks[char] = callback


async def _no_wait(_: float) -> None:
    await asyncio.sleep(0)


async def test_ble_device_sets_up_reads_and_controls() -> None:
    features = struct.pack("<II", 1 << 14, (1 << 3) | (1 << 13))
    trainer, strap = FakeClient(features), FakeClient(b"")
    clients = iter([trainer, strap])
    dev = FtmsBleDevice(
        "AA",
        hr_address="BB",
        sleep=_no_wait,
        client_factory=lambda addr, lost: next(clients),
    )
    await dev.connect()
    assert trainer.writes == [b"\x00", b"\x07"]  # request control, start
    trainer.callbacks[ftms.INDOOR_BIKE_DATA](
        None, bytearray(struct.pack("<HHHh", (1 << 2) | (1 << 6), 3000, 170, 233))
    )
    strap.callbacks[ftms.HR_MEASUREMENT](None, bytearray([0, 151]))
    sample = await anext(dev.telemetry())
    assert (sample.power, sample.cadence, sample.heart_rate) == (233, 85, 151)
    assert sample.speed is None  # physics owns speed
    await dev.set_grade(5.0)
    await dev.set_target_power(200)
    assert trainer.writes[2:] == [ftms.set_simulation(5.0), ftms.set_target_power(200)]
    await dev.disconnect()
    assert not trainer.connected and not strap.connected


async def test_ble_device_skips_unsupported_commands() -> None:
    trainer = FakeClient(struct.pack("<II", 0, 0))
    dev = FtmsBleDevice("AA", sleep=_no_wait, client_factory=lambda a, lost: trainer)
    await dev.connect()
    await dev.set_grade(5.0)
    await dev.set_target_power(200)
    assert trainer.writes == [b"\x00", b"\x07"]


async def test_ble_device_reconnects_after_a_drop() -> None:
    features = struct.pack("<II", 0, 1 << 3)
    first, second = FakeClient(features), FakeClient(features, fail_connect=1)
    made: list[FakeClient] = []

    def factory(addr: str, lost: Callable[[Any], None]) -> FakeClient:
        client = first if not made else second
        made.append(client)
        return client

    dev = FtmsBleDevice("AA", sleep=_no_wait, client_factory=factory)
    await dev.connect()
    first.connected = False
    dev._on_trainer_lost(None)
    for _ in range(20):
        await asyncio.sleep(0)
    assert second.connected  # the first retry failed, the second worked
    assert second.writes == [b"\x00", b"\x07"]
    await dev.disconnect()
