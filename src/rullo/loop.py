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

"""Tick loop: device telemetry -> bus, with distance integrated from speed."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from rullo.bus import TelemetryBus
from rullo.devices.base import Device
from rullo.physics import RiderParams, speed_from_power
from rullo.telemetry import Telemetry


@dataclass
class SessionState:
    """Mutable session state updated each tick."""

    speed: float = 0.0  # m/s
    distance: float = 0.0  # m
    elapsed: float = 0.0  # s


async def run_loop(
    device: Device,
    bus: TelemetryBus,
    params: RiderParams | None = None,
    grade: float = 0.0,
    max_ticks: int | None = None,
    duration_s: float | None = None,
) -> SessionState:
    """Pull telemetry, compute speed, publish enriched samples, integrate distance."""
    rider = params or RiderParams()
    state = SessionState()
    prev_t: float | None = None
    ticks = 0
    await device.connect()
    try:
        async for sample in device.telemetry():
            dt = 0.0 if prev_t is None else sample.t - prev_t
            prev_t = sample.t
            state.speed = speed_from_power(
                sample.power or 0.0, grade, state.speed, rider, dt
            )
            state.distance += state.speed * dt
            state.elapsed = sample.t
            bus.publish(
                Telemetry(
                    t=sample.t,
                    power=sample.power,
                    cadence=sample.cadence,
                    heart_rate=sample.heart_rate,
                    speed=state.speed,
                )
            )
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            if duration_s is not None and state.elapsed >= duration_s:
                break
    finally:
        await device.disconnect()
    return state


async def run(
    device: Device,
    params: RiderParams | None = None,
    course_grade: float = 0.0,
    duration_s: float = 60.0,
) -> SessionState:
    """Run a session against ``device``, printing a one-line HUD per tick."""
    bus = TelemetryBus()
    hud = bus.subscribe()
    printer = asyncio.ensure_future(_print_hud(hud, course_grade))
    await device.set_grade(course_grade)
    try:
        return await run_loop(
            device, bus, params=params, grade=course_grade, duration_s=duration_s
        )
    finally:
        printer.cancel()
        await asyncio.gather(printer, return_exceptions=True)


async def _print_hud(samples: AsyncIterator[Telemetry], course_grade: float) -> None:
    distance_m = 0.0
    prev_t: float | None = None
    async for sample in samples:
        dt = 0.0 if prev_t is None else sample.t - prev_t
        prev_t = sample.t
        distance_m += (sample.speed or 0.0) * dt
        speed_kmh = (sample.speed or 0.0) * 3.6
        print(
            f"P:{sample.power or 0:>4}W  C:{sample.cadence or 0:>3}rpm  "
            f"HR:{sample.heart_rate or 0:>3}bpm  V:{speed_kmh:5.1f}km/h  "
            f"G:{course_grade:+.1f}%  D:{distance_m / 1000.0:6.3f}km"
        )
