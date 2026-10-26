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

"""BLE trainer adapter tests against fake Bluetooth clients."""

import asyncio
import struct
from collections.abc import Callable
from typing import Any

import pytest

from rullo.devices import ftms, wahoo
from rullo.devices.ble import BleTrainer, classify


class FakeServices:
    def __init__(self, chars: set[str]) -> None:
        self.chars = chars

    def get_characteristic(self, specifier: str) -> Any:
        return specifier if specifier in self.chars else None


FTMS_CHARS = {ftms.CONTROL_POINT, ftms.INDOOR_BIKE_DATA, ftms.FTMS_FEATURE}
WAHOO_CHARS = {wahoo.WAHOO_CONTROL, wahoo.CYCLING_POWER_MEASUREMENT}


class FakeClient:
    def __init__(
        self, chars: set[str], features: bytes = b"", fail_connect: int = 0
    ) -> None:
        self.services = FakeServices(chars)
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


def _factory(
    *clients: FakeClient,
) -> Callable[[str, Callable[[Any], None]], FakeClient]:
    queue = list(clients)
    return lambda address, lost: queue.pop(0) if len(queue) > 1 else queue[0]


def test_classify_advertisements() -> None:
    assert classify("KICKR SNAP 0E62", {wahoo.CYCLING_POWER_SERVICE}) == (
        "trainer",
        "Wahoo trainer",
    )
    assert classify("Power meter", {wahoo.CYCLING_POWER_SERVICE}) is None
    both = {ftms.FTMS_SERVICE, wahoo.CYCLING_POWER_SERVICE}
    assert classify("Elite", both) == ("trainer", "FTMS trainer")
    assert classify("TICKR", {ftms.HR_SERVICE}) == ("heart_rate", "Heart-rate strap")
    assert classify("RPM", {wahoo.CSC_SERVICE}) == ("cadence", "Cadence sensor")


async def test_ftms_trainer_with_hr_strap() -> None:
    features = struct.pack("<II", 1 << 14, (1 << 3) | (1 << 13))
    trainer = FakeClient(FTMS_CHARS, features)
    strap = FakeClient(set())
    dev = BleTrainer(
        "AA", hr_address="BB", sleep=_no_wait, client_factory=_factory(trainer, strap)
    )
    await dev.connect()
    assert dev.protocol == "ftms"
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


async def test_ftms_skips_unsupported_commands() -> None:
    trainer = FakeClient(FTMS_CHARS, struct.pack("<II", 0, 0))
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(trainer))
    await dev.connect()
    await dev.set_grade(5.0)
    await dev.set_target_power(200)
    assert trainer.writes == [b"\x00", b"\x07"]


async def test_ftms_is_preferred_when_both_are_offered() -> None:
    trainer = FakeClient(FTMS_CHARS | WAHOO_CHARS, struct.pack("<II", 0, 1 << 3))
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(trainer))
    await dev.connect()
    assert dev.protocol == "ftms"


async def test_wahoo_trainer_power_cadence_and_control() -> None:
    trainer = FakeClient(WAHOO_CHARS)
    cadence = FakeClient(set())
    now = [100.0]
    dev = BleTrainer(
        "C2:AF",
        cadence_address="CC",
        mass_kg=72.0,
        sleep=_no_wait,
        clock=lambda: now[0],
        client_factory=_factory(trainer, cadence),
    )
    await dev.connect()
    assert dev.protocol == "wahoo"
    assert trainer.writes == [wahoo.unlock()]
    trainer.callbacks[wahoo.CYCLING_POWER_MEASUREMENT](
        None, bytearray(struct.pack("<HhHIH", 0x14, 212, 0, 10, 100))
    )
    csc = cadence.callbacks[wahoo.CSC_MEASUREMENT]
    csc(None, bytearray(struct.pack("<BHH", 2, 10, 1024)))
    now[0] = 100.7
    csc(None, bytearray(struct.pack("<BHH", 2, 11, 1024 + 683)))
    sample = await anext(dev.telemetry())
    assert (sample.power, sample.cadence) == (212, 90)

    await dev.set_grade(4.0)  # first grade enters sim mode with the rider's weight
    await dev.set_grade(6.0)
    await dev.set_target_power(230)  # ERG
    await dev.set_grade(2.0)  # back to sim mode
    assert trainer.writes[1:] == [
        wahoo.sim_mode(72.0),
        wahoo.grade(4.0),
        wahoo.grade(6.0),
        wahoo.erg(230),
        wahoo.sim_mode(72.0),
        wahoo.grade(2.0),
    ]


async def test_wahoo_without_cadence_reports_none() -> None:
    trainer = FakeClient(WAHOO_CHARS)
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(trainer))
    await dev.connect()
    trainer.callbacks[wahoo.CYCLING_POWER_MEASUREMENT](
        None, bytearray(bytes.fromhex("14000000000000000000c403"))
    )
    sample = await anext(dev.telemetry())
    assert sample.power == 0 and sample.cadence is None


async def test_reconnect_reapplies_the_last_command() -> None:
    first = FakeClient(WAHOO_CHARS)
    second = FakeClient(WAHOO_CHARS, fail_connect=1)
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(first, second))
    await dev.connect()
    await dev.set_target_power(250)
    first.connected = False
    dev._on_trainer_lost(None)
    for _ in range(30):
        await asyncio.sleep(0)
    assert second.connected  # the first retry failed, the second worked
    assert second.writes == [wahoo.unlock(), wahoo.erg(250)]
    await dev.disconnect()


async def test_trainer_without_any_control_is_rejected() -> None:
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(FakeClient(set())))
    with pytest.raises(ConnectionError, match="neither FTMS nor Wahoo"):
        await dev.connect()


async def test_connect_failure_explains_the_usual_cause() -> None:
    trainer = FakeClient(WAHOO_CHARS, fail_connect=1)
    dev = BleTrainer("AA", sleep=_no_wait, client_factory=_factory(trainer))
    with pytest.raises(ConnectionError, match="system Bluetooth settings"):
        await dev.connect()
