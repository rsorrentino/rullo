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

"""Bluetooth LE adapter: an FTMS smart trainer plus an optional heart-rate strap.

This is the only module that imports ``bleak``. Reconnection lives here, not in
the app: if the trainer drops out, samples carry no power until it is back.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from rullo.devices import ftms
from rullo.telemetry import Telemetry

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 2.0


class Client(Protocol):
    """The slice of ``bleak.BleakClient`` the adapter uses."""

    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> Any: ...

    async def disconnect(self) -> Any: ...

    async def read_gatt_char(self, char: str) -> bytearray: ...

    async def write_gatt_char(self, char: str, data: bytes, response: bool) -> None: ...

    async def start_notify(
        self, char: str, callback: Callable[[Any, bytearray], None]
    ) -> None: ...


ClientFactory = Callable[[str, Callable[[Any], None]], Client]


def bleak_client(address: str, on_disconnect: Callable[[Any], None]) -> Client:
    from bleak import BleakClient

    return cast(Client, BleakClient(address, disconnected_callback=on_disconnect))


@dataclass(frozen=True)
class Found:
    address: str
    name: str
    kind: str  # "trainer" or "heart_rate"
    rssi: int | None


async def scan(duration: float = 5.0) -> list[Found]:
    """Find nearby FTMS trainers and heart-rate straps."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=duration, return_adv=True)
    result = []
    for address, (device, adv) in found.items():
        uuids = {u.lower() for u in adv.service_uuids}
        kind = (
            "trainer"
            if ftms.FTMS_SERVICE in uuids
            else "heart_rate"
            if ftms.HR_SERVICE in uuids
            else None
        )
        if kind:
            name = device.name or adv.local_name or address
            result.append(Found(address, name, kind, adv.rssi))
    return sorted(result, key=lambda f: (f.kind, -(f.rssi or -999)))


class FtmsBleDevice:
    """Implements ``Device`` for an FTMS trainer, optionally merging an HR strap."""

    def __init__(
        self,
        address: str,
        hr_address: str | None = None,
        rate_hz: float = 4.0,
        client_factory: ClientFactory = bleak_client,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._address = address
        self._hr_address = hr_address
        self._rate_hz = rate_hz
        self._factory = client_factory
        self._sleep = sleep
        self._clock = clock
        self._trainer: Client | None = None
        self._strap: Client | None = None
        self.features: ftms.Features | None = None
        self._bike = ftms.BikeData()
        self._strap_hr: int | None = None
        self._running = False
        self._reconnect: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self._running = True
        self._trainer = self._factory(self._address, self._on_trainer_lost)
        await self._setup_trainer(self._trainer)
        if self._hr_address:
            self._strap = self._factory(self._hr_address, lambda _: None)
            try:
                await self._strap.connect()
                await self._strap.start_notify(ftms.HR_MEASUREMENT, self._on_hr)
            except Exception:  # a missing strap must not stop the ride
                logger.exception("heart-rate strap %s unavailable", self._hr_address)
                self._strap = None

    async def _setup_trainer(self, client: Client) -> None:
        await client.connect()
        self.features = ftms.parse_features(
            bytes(await client.read_gatt_char(ftms.FTMS_FEATURE))
        )
        await client.start_notify(ftms.INDOOR_BIKE_DATA, self._on_bike)
        await client.start_notify(ftms.CONTROL_POINT, self._on_control)
        await client.write_gatt_char(ftms.CONTROL_POINT, ftms.request_control(), True)
        await client.write_gatt_char(ftms.CONTROL_POINT, ftms.start_or_resume(), True)
        logger.info("trainer %s connected: %s", self._address, self.features)

    def _on_bike(self, _: Any, data: bytearray) -> None:
        self._bike = ftms.parse_indoor_bike_data(bytes(data))

    def _on_hr(self, _: Any, data: bytearray) -> None:
        self._strap_hr = ftms.parse_heart_rate(bytes(data))

    def _on_control(self, _: Any, data: bytearray) -> None:
        response = ftms.parse_response(bytes(data))
        if response and response[1] != ftms.RESULT_SUCCESS:
            logger.warning("trainer rejected op 0x%02x (result %s)", *response)

    def _on_trainer_lost(self, _: Any) -> None:
        if self._running and (self._reconnect is None or self._reconnect.done()):
            logger.warning("trainer %s disconnected; reconnecting", self._address)
            self._bike = ftms.BikeData()
            self._reconnect = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._running:
            await self._sleep(_RECONNECT_DELAY_S)
            try:
                self._trainer = self._factory(self._address, self._on_trainer_lost)
                await self._setup_trainer(self._trainer)
                return
            except Exception as exc:
                logger.info("reconnect failed: %s", exc)

    async def telemetry(self) -> AsyncIterator[Telemetry]:
        dt = 1.0 / self._rate_hz
        start = self._clock()
        while self._running:
            bike = self._bike
            yield Telemetry(
                t=self._clock() - start,
                power=bike.power,
                cadence=bike.cadence,
                heart_rate=self._strap_hr or bike.heart_rate,
                speed=None,  # the physics model owns speed
            )
            await self._sleep(dt)

    async def _control(self, payload: bytes) -> None:
        client = self._trainer
        if client is None or not client.is_connected:
            return
        try:
            await client.write_gatt_char(ftms.CONTROL_POINT, payload, True)
        except Exception as exc:
            logger.warning("control point write failed: %s", exc)

    async def set_grade(self, percent: float) -> None:
        if self.features is not None and self.features.simulation:
            await self._control(ftms.set_simulation(percent))

    async def set_target_power(self, watts: int) -> None:
        if self.features is not None and self.features.target_power:
            await self._control(ftms.set_target_power(watts))

    async def disconnect(self) -> None:
        self._running = False
        if self._reconnect is not None:
            self._reconnect.cancel()
        for client in (self._trainer, self._strap):
            if client is not None and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception("disconnect failed")
