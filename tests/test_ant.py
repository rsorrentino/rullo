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

"""ANT+ adapter tests against a fake USB transport."""

import asyncio
import threading

import pytest

from rullo.devices import antplus
from rullo.devices.ant import AntTrainer, ChannelSpec, is_ant, parse_id, scan


class FakeTransport:
    """Delivers pages from a separate thread, like openant does."""

    def __init__(self, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.opened = threading.Event()
        self.specs: dict[str, ChannelSpec] = {}
        self.sent: list[tuple[str, bytes]] = []
        self.closed = False

    def open(self, channels: list[ChannelSpec]) -> None:
        if self.fail_open:
            raise RuntimeError("No supported devices found")
        self.specs = {c.key: c for c in channels}
        self.opened.set()

    def push(self, key: str, page: bytes, number: int = 4242) -> None:
        thread = threading.Thread(target=self.specs[key].on_data, args=(page, number))
        thread.start()
        thread.join()

    def send(self, key: str, page: bytes) -> None:
        self.sent.append((key, page))

    def close(self) -> None:
        self.closed = True


async def _no_wait(_: float) -> None:
    await asyncio.sleep(0)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_ids() -> None:
    assert parse_id("ant:17:12345") == (17, 12345)
    assert is_ant("ant:120:1") and not is_ant("C2:AF:AB:5E:07:46") and not is_ant(None)
    with pytest.raises(ValueError):
        parse_id("ant:99:1")


async def test_fec_trainer_with_sensors_and_control() -> None:
    transport = FakeTransport()
    now = [10.0]
    dev = AntTrainer(
        "ant:17:100",
        hr="ant:120:200",
        cadence="ant:122:300",
        mass_kg=80.0,
        transport=transport,
        sleep=_no_wait,
        clock=lambda: now[0],
    )
    await dev.connect()
    assert {
        k: (s.device_type, s.device_number) for k, s in transport.specs.items()
    } == {"trainer": (17, 100), "heart_rate": (120, 200), "cadence": (122, 300)}
    transport.push("trainer", bytes([0x19, 1, 85, 0, 0, 0xC8, 0x00, 0]))  # 200 W
    transport.push("heart_rate", bytes([4, 0, 0, 0, 0, 0, 0, 133]))
    transport.push("cadence", bytes([0, 0, 0, 0, 0x00, 0x04, 10, 0]))
    now[0] = 10.7
    transport.push("cadence", bytes([0, 0, 0, 0, 0xAB, 0x06, 11, 0]))  # +683 ticks
    await _settle()
    sample = await anext(dev.telemetry())
    assert (sample.power, sample.heart_rate, sample.cadence) == (200, 133, 90)

    await dev.set_grade(3.0)
    await dev.set_target_power(220)
    assert transport.sent == [
        (
            "trainer",
            antplus.user_configuration(80.0),
        ),  # weight before the first command
        ("trainer", antplus.track_resistance(3.0)),
        ("trainer", antplus.target_power(220)),
    ]
    await dev.disconnect()
    assert transport.closed


async def test_power_meter_is_read_only() -> None:
    transport = FakeTransport()
    dev = AntTrainer("ant:11:5", transport=transport, sleep=_no_wait)
    await dev.connect()
    assert not dev.controllable
    transport.push("trainer", bytes([0x10, 1, 0xFF, 91, 0, 0, 0x04, 0x01]))  # 260 W
    await _settle()
    sample = await anext(dev.telemetry())
    assert (sample.power, sample.cadence) == (260, 91)
    await dev.set_grade(5.0)
    assert transport.sent == []


async def test_missing_stick_explains_what_to_check() -> None:
    dev = AntTrainer("ant:17:1", transport=FakeTransport(fail_open=True))
    with pytest.raises(ConnectionError, match="USB stick"):
        await dev.connect()


async def test_scan_reports_each_device_heard() -> None:
    transport = FakeTransport()

    async def drive() -> None:
        await asyncio.to_thread(transport.opened.wait, 5)
        transport.push(str(antplus.FE_C), bytes(8), number=100)
        transport.push(str(antplus.FE_C), bytes(8), number=100)  # heard twice
        transport.push(str(antplus.HEART_RATE), bytes(8), number=200)
        transport.push(str(antplus.CADENCE), bytes(8), number=0)  # not yet paired

    driver = asyncio.ensure_future(drive())
    found = await scan(0.05, transport_factory=lambda: transport)
    await driver
    assert [(f.address, f.kind) for f in found] == [
        ("ant:120:200", "heart_rate"),
        ("ant:17:100", "trainer"),
    ]
    assert transport.closed
