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

"""Bluetooth LE adapter: a smart trainer plus optional heart-rate and cadence sensors.

This is the only module that imports ``bleak``. The trainer protocol is chosen
from its GATT services when it connects: FTMS when offered, otherwise Wahoo's
vendor control on the Cycling Power service (KICKR trainers on older firmware).
Reconnection lives here, not in the app: if the trainer drops out, samples carry
no power until it is back.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from rullo.devices import ftms, wahoo
from rullo.telemetry import Telemetry

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 2.0


class Services(Protocol):
    def get_characteristic(self, specifier: str) -> Any: ...


class Client(Protocol):
    """The slice of ``bleak.BleakClient`` the adapter uses."""

    @property
    def is_connected(self) -> bool: ...

    @property
    def services(self) -> Services: ...

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
    kind: str  # "trainer", "heart_rate" or "cadence"
    rssi: int | None
    detail: str = ""


def classify(name: str, uuids: set[str]) -> tuple[str, str] | None:
    """(kind, detail) for an advertisement, or None if Rullo cannot use it."""
    if ftms.FTMS_SERVICE in uuids:
        return "trainer", "FTMS trainer"
    if wahoo.CYCLING_POWER_SERVICE in uuids and name.upper().startswith("KICKR"):
        return "trainer", "Wahoo trainer"
    if ftms.HR_SERVICE in uuids:
        return "heart_rate", "Heart-rate strap"
    if wahoo.CSC_SERVICE in uuids:
        return "cadence", "Cadence sensor"
    return None


async def scan(duration: float = 5.0) -> list[Found]:
    """Find nearby trainers, heart-rate straps and cadence sensors."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=duration, return_adv=True)
    result = []
    for address, (device, adv) in found.items():
        name = device.name or adv.local_name or address
        kind = classify(name, {u.lower() for u in adv.service_uuids})
        if kind:
            result.append(Found(address, name, kind[0], adv.rssi, kind[1]))
    return sorted(result, key=lambda f: (f.kind, -(f.rssi or -999)))


class BleTrainer:
    """Implements ``Device`` for an FTMS or Wahoo trainer plus optional sensors."""

    def __init__(
        self,
        address: str,
        hr_address: str | None = None,
        cadence_address: str | None = None,
        mass_kg: float = 78.0,
        rate_hz: float = 4.0,
        client_factory: ClientFactory = bleak_client,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._address = address
        self._hr_address = hr_address
        self._cadence_address = cadence_address
        self._mass_kg = mass_kg
        self._rate_hz = rate_hz
        self._factory = client_factory
        self._sleep = sleep
        self._clock = clock
        self._trainer: Client | None = None
        self._sensors: list[Client] = []
        self.protocol: str | None = None  # "ftms" or "wahoo" once connected
        self.features: ftms.Features | None = None
        self._power: int | None = None
        self._trainer_cadence: int | None = None
        self._trainer_hr: int | None = None
        self._strap_hr: int | None = None
        self._trainer_crank = wahoo.CadenceMeter()
        self._sensor_crank = wahoo.CadenceMeter()
        self._wahoo_mode: str | None = None  # "sim" or "erg"
        self._last_command: tuple[str, float] | None = None  # re-sent on reconnect
        self._write_lock = asyncio.Lock()
        self._running = False
        self._reconnect: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self._running = True
        self._trainer = self._factory(self._address, self._on_trainer_lost)
        await self._setup_trainer(self._trainer)
        for address, char, handler in (
            (self._hr_address, ftms.HR_MEASUREMENT, self._on_hr),
            (self._cadence_address, wahoo.CSC_MEASUREMENT, self._on_csc),
        ):
            if not address:
                continue
            sensor = self._factory(address, lambda _: None)
            try:
                await sensor.connect()
                await sensor.start_notify(char, handler)
                self._sensors.append(sensor)
            except Exception:  # a missing sensor must not stop the ride
                logger.exception("sensor %s unavailable", address)

    async def _setup_trainer(self, client: Client) -> None:
        try:
            await client.connect()
        except Exception as exc:
            # A trainer held by the desktop's Bluetooth settings or another app
            # stops advertising, which is the usual cause of "not found".
            raise ConnectionError(
                f"could not connect to the trainer ({exc}). If it is connected in "
                "your system Bluetooth settings or another app (Wahoo, Zwift), "
                "disconnect it there and try again."
            ) from exc
        services = client.services
        if services.get_characteristic(ftms.CONTROL_POINT) is not None:
            await self._setup_ftms(client)
        elif services.get_characteristic(wahoo.WAHOO_CONTROL) is not None:
            await self._setup_wahoo(client)
        else:
            await client.disconnect()
            raise ConnectionError(
                f"{self._address} offers neither FTMS nor Wahoo trainer control"
            )
        logger.info("trainer %s connected using %s", self._address, self.protocol)

    async def _setup_ftms(self, client: Client) -> None:
        self.protocol = "ftms"
        self.features = ftms.parse_features(
            bytes(await client.read_gatt_char(ftms.FTMS_FEATURE))
        )
        await client.start_notify(ftms.INDOOR_BIKE_DATA, self._on_bike)
        await client.start_notify(ftms.CONTROL_POINT, self._on_ftms_control)
        await client.write_gatt_char(ftms.CONTROL_POINT, ftms.request_control(), True)
        await client.write_gatt_char(ftms.CONTROL_POINT, ftms.start_or_resume(), True)

    async def _setup_wahoo(self, client: Client) -> None:
        self.protocol = "wahoo"
        self._wahoo_mode = None
        await client.start_notify(wahoo.CYCLING_POWER_MEASUREMENT, self._on_power)
        await client.start_notify(wahoo.WAHOO_CONTROL, self._on_wahoo_control)
        await client.write_gatt_char(wahoo.WAHOO_CONTROL, wahoo.unlock(), True)

    # --- notifications -------------------------------------------------------

    def _on_bike(self, _: Any, data: bytearray) -> None:
        bike = ftms.parse_indoor_bike_data(bytes(data))
        self._power, self._trainer_cadence = bike.power, bike.cadence
        self._trainer_hr = bike.heart_rate

    def _on_power(self, _: Any, data: bytearray) -> None:
        reading = wahoo.parse_cycling_power(bytes(data))
        self._power = reading.power
        if reading.crank is not None:
            self._trainer_crank.update(reading.crank, self._clock())

    def _on_hr(self, _: Any, data: bytearray) -> None:
        self._strap_hr = ftms.parse_heart_rate(bytes(data))

    def _on_csc(self, _: Any, data: bytearray) -> None:
        crank = wahoo.parse_csc(bytes(data))
        if crank is not None:
            self._sensor_crank.update(crank, self._clock())

    def _on_ftms_control(self, _: Any, data: bytearray) -> None:
        response = ftms.parse_response(bytes(data))
        if response and response[1] != ftms.RESULT_SUCCESS:
            logger.warning("trainer rejected op 0x%02x (result %s)", *response)

    def _on_wahoo_control(self, _: Any, data: bytearray) -> None:
        logger.debug("wahoo control response %s", bytes(data).hex())

    # --- connection loss -----------------------------------------------------

    def _on_trainer_lost(self, _: Any) -> None:
        if self._running and (self._reconnect is None or self._reconnect.done()):
            logger.warning("trainer %s disconnected; reconnecting", self._address)
            self._power = self._trainer_cadence = self._trainer_hr = None
            self._reconnect = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._running:
            await self._sleep(_RECONNECT_DELAY_S)
            try:
                self._trainer = self._factory(self._address, self._on_trainer_lost)
                await self._setup_trainer(self._trainer)
            except Exception as exc:
                logger.info("reconnect failed: %s", exc)
                continue
            if self._last_command is not None:  # the trainer forgot it
                kind, value = self._last_command
                if kind == "grade":
                    await self.set_grade(value)
                else:
                    await self.set_target_power(int(value))
            return

    # --- Device protocol -----------------------------------------------------

    async def telemetry(self) -> AsyncIterator[Telemetry]:
        dt = 1.0 / self._rate_hz
        start = self._clock()
        while self._running:
            now = self._clock()
            cadence = self._sensor_crank.current(now)
            if cadence is None:
                cadence = self._trainer_cadence
            if cadence is None:
                cadence = self._trainer_crank.current(now)
            yield Telemetry(
                t=now - start,
                power=self._power,
                cadence=cadence,
                heart_rate=self._strap_hr or self._trainer_hr,
                speed=None,  # the physics model owns speed
            )
            await self._sleep(dt)

    async def _write(self, char: str, payload: bytes) -> bool:
        client = self._trainer
        if client is None or not client.is_connected:
            return False
        async with self._write_lock:
            try:
                await client.write_gatt_char(char, payload, True)
                return True
            except Exception as exc:
                logger.warning("trainer write failed: %s", exc)
                return False

    async def set_grade(self, percent: float) -> None:
        self._last_command = ("grade", percent)
        if self.protocol == "ftms":
            if self.features is not None and self.features.simulation:
                await self._write(ftms.CONTROL_POINT, ftms.set_simulation(percent))
        elif self.protocol == "wahoo":
            if self._wahoo_mode != "sim":
                if not await self._write(
                    wahoo.WAHOO_CONTROL, wahoo.sim_mode(self._mass_kg)
                ):
                    return
                self._wahoo_mode = "sim"
            await self._write(wahoo.WAHOO_CONTROL, wahoo.grade(percent))

    async def set_target_power(self, watts: int) -> None:
        self._last_command = ("power", float(watts))
        if self.protocol == "ftms":
            if self.features is not None and self.features.target_power:
                await self._write(ftms.CONTROL_POINT, ftms.set_target_power(watts))
        elif self.protocol == "wahoo":
            if await self._write(wahoo.WAHOO_CONTROL, wahoo.erg(watts)):
                self._wahoo_mode = "erg"

    async def disconnect(self) -> None:
        self._running = False
        if self._reconnect is not None:
            self._reconnect.cancel()
        for client in [self._trainer, *self._sensors]:
            if client is not None and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception("disconnect failed")
