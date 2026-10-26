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

"""ANT+ adapter: an FE-C trainer (or a power meter) plus HR and cadence sensors.

This is the only module that imports ``openant``. openant runs its own thread,
so readings are handed to the event loop with ``call_soon_threadsafe`` and
blocking sends run in a worker thread. Device ids look like ``ant:17:12345``
(device type and device number).
"""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from rullo.devices import antplus
from rullo.devices.ble import Found
from rullo.devices.wahoo import CadenceMeter
from rullo.telemetry import Telemetry

logger = logging.getLogger(__name__)


def parse_id(device_id: str) -> tuple[int, int]:
    """(device type, device number) from an ``ant:<type>:<number>`` id."""
    prefix, kind, number = device_id.split(":")
    if prefix != "ant" or int(kind) not in antplus.KINDS:
        raise ValueError(f"not an ANT+ device id: {device_id!r}")
    return int(kind), int(number)


def is_ant(device_id: str | None) -> bool:
    return bool(device_id) and str(device_id).startswith("ant:")


@dataclass(frozen=True)
class ChannelSpec:
    key: str  # "trainer", "heart_rate" or "cadence"
    device_type: int
    device_number: int  # 0 = the first device of this type found
    on_data: Callable[[bytes, int], None]  # (8-byte page, device number)


class Transport(Protocol):
    """Opens ANT+ receive channels on a USB stick and sends acknowledged pages."""

    def open(self, channels: list[ChannelSpec]) -> None: ...

    def send(self, key: str, page: bytes) -> None: ...

    def close(self) -> None: ...


class OpenAntTransport:
    """``Transport`` over openant's Node; every method blocks (call from a thread)."""

    def __init__(self) -> None:
        self._node: Any = None
        self._thread: threading.Thread | None = None
        self._channels: dict[str, Any] = {}

    def open(self, channels: list[ChannelSpec]) -> None:
        from openant.devices import ANTPLUS_NETWORK_KEY
        from openant.easy.channel import Channel
        from openant.easy.node import Node

        node = Node()  # raises if no ANT+ USB stick is plugged in
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        for spec in channels:
            channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE, 0x00, 0x01)

            def receive(data: Any, spec: ChannelSpec = spec) -> None:
                raw = bytes(data)
                number = raw[9] | (raw[10] << 8) if len(raw) > 10 else 0
                spec.on_data(raw[:8], number)

            channel.on_broadcast_data = receive
            channel.on_burst_data = receive
            channel.set_search_timeout(0xFF)
            channel.set_id(spec.device_number, spec.device_type, 0)
            channel.enable_extended_messages(1)
            channel.set_period(antplus.PERIODS[spec.device_type])
            channel.set_rf_freq(57)
            channel.open()
            self._channels[spec.key] = channel
        self._node = node
        self._thread = threading.Thread(
            target=node.start, name="rullo-ant", daemon=True
        )
        self._thread.start()

    def send(self, key: str, page: bytes) -> None:
        self._channels[key].send_acknowledged_data(list(page))

    def close(self) -> None:
        if self._node is not None:
            self._node.stop()
            self._node = None


class AntTrainer:
    """Implements ``Device`` over ANT+: FE-C or a power meter, plus sensors."""

    def __init__(
        self,
        trainer: str,
        hr: str | None = None,
        cadence: str | None = None,
        mass_kg: float = 78.0,
        rate_hz: float = 4.0,
        transport: Transport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._trainer_type, self._trainer_number = parse_id(trainer)
        self._hr = parse_id(hr) if hr else None
        self._cadence = parse_id(cadence) if cadence else None
        self._mass_kg = mass_kg
        self._rate_hz = rate_hz
        self._transport = transport or OpenAntTransport()
        self._sleep = sleep
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._power: int | None = None
        self._trainer_cadence: int | None = None
        self._hr_value: int | None = None
        self._crank = CadenceMeter()
        self._configured = False  # FE-C user configuration sent
        self._running = False

    @property
    def controllable(self) -> bool:
        return self._trainer_type == antplus.FE_C

    def _specs(self) -> list[ChannelSpec]:
        specs = [
            ChannelSpec(
                "trainer",
                self._trainer_type,
                self._trainer_number,
                self._threadsafe(self._on_trainer),
            )
        ]
        if self._hr:
            specs.append(
                ChannelSpec("heart_rate", *self._hr, self._threadsafe(self._on_hr))
            )
        if self._cadence:
            specs.append(
                ChannelSpec(
                    "cadence", *self._cadence, self._threadsafe(self._on_cadence)
                )
            )
        return specs

    def _threadsafe(
        self, handler: Callable[[bytes], None]
    ) -> Callable[[bytes, int], None]:
        def deliver(page: bytes, number: int) -> None:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(handler, page)

        return deliver

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True
        try:
            await asyncio.to_thread(self._transport.open, self._specs())
        except Exception as exc:
            raise ConnectionError(
                f"could not open the ANT+ USB stick ({exc}). Check that it is "
                "plugged in, not used by another app, and that its udev rule is "
                "installed (see the README)."
            ) from exc

    def _on_trainer(self, page: bytes) -> None:
        if self._trainer_type == antplus.FE_C:
            reading = antplus.parse_fec(page)
        else:
            reading = antplus.parse_power_meter(page)
        if reading is not None:
            self._power, self._trainer_cadence = reading.power, reading.cadence

    def _on_hr(self, page: bytes) -> None:
        self._hr_value = antplus.parse_heart_rate(page)

    def _on_cadence(self, page: bytes) -> None:
        assert self._cadence is not None
        self._crank.update(antplus.parse_cadence(self._cadence[0], page), self._clock())

    async def telemetry(self) -> AsyncIterator[Telemetry]:
        dt = 1.0 / self._rate_hz
        start = self._clock()
        while self._running:
            now = self._clock()
            cadence = self._crank.current(now) if self._cadence else None
            yield Telemetry(
                t=now - start,
                power=self._power,
                cadence=cadence if cadence is not None else self._trainer_cadence,
                heart_rate=self._hr_value,
                speed=None,  # the physics model owns speed
            )
            await self._sleep(dt)

    async def _send(self, page: bytes) -> None:
        if not self.controllable or not self._running:
            return
        try:
            if not self._configured:  # rider weight first, so grade feels right
                await asyncio.to_thread(
                    self._transport.send,
                    "trainer",
                    antplus.user_configuration(self._mass_kg),
                )
                self._configured = True
            await asyncio.to_thread(self._transport.send, "trainer", page)
        except Exception as exc:  # not paired yet, or a missed acknowledgement
            logger.warning("ANT+ send failed: %s", exc)

    async def set_grade(self, percent: float) -> None:
        await self._send(antplus.track_resistance(percent))

    async def set_target_power(self, watts: int) -> None:
        await self._send(antplus.target_power(watts))

    async def disconnect(self) -> None:
        self._running = False
        await asyncio.to_thread(self._transport.close)


async def scan(
    duration: float = 5.0,
    transport_factory: Callable[[], Transport] = OpenAntTransport,
) -> list[Found]:
    """Listen on one wildcard channel per device type; report each device heard."""
    loop = asyncio.get_running_loop()
    found: dict[tuple[int, int], Found] = {}

    def heard(device_type: int) -> Callable[[bytes, int], None]:
        def record(number: int) -> None:
            if number and (device_type, number) not in found:
                kind, detail = antplus.KINDS[device_type]
                found[(device_type, number)] = Found(
                    f"ant:{device_type}:{number}", f"ANT+ {number}", kind, None, detail
                )

        def on_data(page: bytes, number: int) -> None:
            loop.call_soon_threadsafe(record, number)

        return on_data

    transport = transport_factory()
    specs = [ChannelSpec(str(t), t, 0, heard(t)) for t in antplus.KINDS]
    await asyncio.to_thread(transport.open, specs)
    try:
        await asyncio.sleep(duration)
    finally:
        await asyncio.to_thread(transport.close)
    return sorted(found.values(), key=lambda f: (f.kind, f.address))
