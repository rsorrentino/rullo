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
from rullo.course import Course
from rullo.devices.base import Device
from rullo.physics import RiderParams, speed_from_power
from rullo.resistance import ResistanceController
from rullo.telemetry import Telemetry


class RideClock:
    """Moving time: starts on the first pedal stroke and stops while paused.

    Workouts and ride durations follow moving time, so pausing does not eat
    into an interval. States: "waiting" (for the first pedal stroke),
    "riding" and "paused".
    """

    def __init__(self, pedal_to_start: bool = True) -> None:
        self.state = "waiting" if pedal_to_start else "riding"
        self.moving = 0.0  # workout position: moving time plus skipped blocks
        self.ridden = 0.0  # time actually ridden (what the rider's clock shows)
        self._prev_t: float | None = None
        self.held = False  # resistance eased off for the current pause

    def tick(self, t: float, power: int | None, cadence: int | None) -> float:
        if self.state == "waiting" and ((power or 0) > 0 or (cadence or 0) > 0):
            self.state = "riding"
            self._prev_t = t  # moving time starts at the first stroke
        if self.state == "riding" and self._prev_t is not None:
            self.moving += t - self._prev_t
            self.ridden += t - self._prev_t
        self._prev_t = t
        return self.moving

    def pause(self) -> None:
        if self.state == "riding":
            self.state, self.held = "paused", False

    def resume(self) -> None:
        if self.state == "paused":
            self.state = "riding"

    def skip_to(self, moving: float) -> None:
        """Jump moving time forward, e.g. to the start of the next workout block."""
        self.moving = max(self.moving, moving)


@dataclass
class SessionState:
    """Mutable session state updated each tick."""

    speed: float = 0.0  # m/s
    distance: float = 0.0  # m
    elapsed: float = 0.0  # s
    grade: float = 0.0  # % at the current position
    elevation: float = 0.0  # m at the current position
    moving: float = 0.0  # s of moving time (equals elapsed without a RideClock)


async def run_loop(
    device: Device,
    bus: TelemetryBus,
    params: RiderParams | None = None,
    grade: float = 0.0,
    target_power: int | None = None,
    max_ticks: int | None = None,
    duration_s: float | None = None,
    state: SessionState | None = None,
    course: Course | None = None,
    controller: ResistanceController | None = None,
    stop: asyncio.Event | None = None,
    clock: RideClock | None = None,
) -> SessionState:
    """Pull telemetry, compute speed, publish enriched samples, integrate distance.

    With a ``course`` the grade follows the rider's position, otherwise it is
    the constant ``grade``. Resistance commands go through ``controller`` every
    tick; without one, ``grade`` and ``target_power`` are sent once at start.
    With a ``clock``, the controller and ``duration_s`` follow moving time, and
    resistance eases off while the ride is paused.
    """
    rider = params or RiderParams()
    state = state if state is not None else SessionState()
    prev_t: float | None = None
    ticks = 0
    await device.connect()
    try:
        if controller is None:
            await device.set_grade(grade)
            if target_power is not None:
                await device.set_target_power(target_power)
        async for sample in device.telemetry():
            dt = 0.0 if prev_t is None else sample.t - prev_t
            prev_t = sample.t
            if course is not None:
                state.grade = course.grade_at(state.distance)
            else:
                state.grade = grade
            state.speed = speed_from_power(
                sample.power or 0.0, state.grade, state.speed, rider, dt
            )
            state.distance += state.speed * dt
            state.elapsed = sample.t
            if course is not None:
                state.elevation = course.elevation_at(state.distance)
            else:
                state.elevation = state.distance * grade / 100.0
            if clock is None:
                state.moving = sample.t
            else:
                state.moving = clock.tick(sample.t, sample.power, sample.cadence)
            if controller is not None:
                if clock is None or clock.state == "riding":
                    await controller.update(state.moving, state.grade, sample.cadence)
                elif clock.state == "paused" and not clock.held:
                    await controller.hold()
                    clock.held = True
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
            if duration_s is not None and state.moving >= duration_s:
                break
            if stop is not None and stop.is_set():
                break
    finally:
        await device.disconnect()
    return state


async def run(
    device: Device,
    params: RiderParams | None = None,
    course_grade: float = 0.0,
    duration_s: float | None = 60.0,
    target_power: int | None = None,
    course: Course | None = None,
    controller: ResistanceController | None = None,
) -> SessionState:
    """Run a session against ``device``, printing a one-line HUD per tick."""
    bus = TelemetryBus()
    state = SessionState()
    hud = bus.subscribe()
    printer = asyncio.ensure_future(_print_hud(hud, state))
    try:
        return await run_loop(
            device,
            bus,
            params=params,
            grade=course_grade,
            target_power=target_power,
            duration_s=duration_s,
            state=state,
            course=course,
            controller=controller,
        )
    finally:
        printer.cancel()
        await asyncio.gather(printer, return_exceptions=True)


async def _print_hud(samples: AsyncIterator[Telemetry], state: SessionState) -> None:
    async for sample in samples:
        speed_kmh = (sample.speed or 0.0) * 3.6
        print(
            f"P:{sample.power or 0:>4}W  C:{sample.cadence or 0:>3}rpm  "
            f"HR:{sample.heart_rate or 0:>3}bpm  V:{speed_kmh:5.1f}km/h  "
            f"G:{state.grade:+.1f}%  D:{state.distance / 1000.0:6.3f}km"
        )
