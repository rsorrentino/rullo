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

"""SimulatedDevice and loop tests."""

from rullo.bus import TelemetryBus
from rullo.devices.base import Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.loop import run_loop
from rullo.telemetry import Telemetry


async def _collect(device: SimulatedDevice, n: int) -> list[Telemetry]:
    await device.connect()
    samples = []
    async for sample in device.telemetry():
        samples.append(sample)
        if len(samples) == n:
            break
    await device.disconnect()
    return samples


async def test_simulated_yields_power() -> None:
    samples = await _collect(SimulatedDevice(rate_hz=100.0), 5)
    assert all(s.power is not None for s in samples)


async def test_simulated_is_deterministic_given_seed() -> None:
    samples_a = await _collect(SimulatedDevice(rate_hz=100.0, seed=42), 10)
    samples_b = await _collect(SimulatedDevice(rate_hz=100.0, seed=42), 10)
    assert [s.power for s in samples_a] == [s.power for s in samples_b]


async def test_erg_mode_converges_to_target() -> None:
    device = SimulatedDevice(mode=Mode.ERG, rider_power=100, rate_hz=100.0)
    await device.set_target_power(300)
    samples = await _collect(device, 30)
    assert samples[-1].power is not None
    assert abs(samples[-1].power - 300) < 10


async def test_bus_fans_out_to_multiple_subscribers() -> None:
    bus = TelemetryBus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()
    sample = Telemetry(t=0.0, power=200, cadence=90, heart_rate=140, speed=5.0)
    bus.publish(sample)
    assert await anext(sub_a) == sample
    assert await anext(sub_b) == sample


async def test_loop_integrates_distance() -> None:
    bus = TelemetryBus()
    received = bus.subscribe()
    state = await run_loop(SimulatedDevice(rate_hz=100.0), bus, max_ticks=20)
    assert state.distance > 0.0
    first = await anext(received)
    assert first.power is not None
    assert first.speed is not None
