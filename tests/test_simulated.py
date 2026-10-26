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
from rullo.devices.simulated import SimulatedDevice
from rullo.loop import run_loop


async def test_simulated_yields_power() -> None:
    device = SimulatedDevice(trace=[150.0, 250.0], rate_hz=100.0)
    await device.connect()
    samples = []
    async for sample in device.telemetry():
        samples.append(sample)
        if len(samples) == 3:
            break
    await device.disconnect()
    assert [s.power for s in samples] == [150.0, 250.0, 150.0]


async def test_loop_integrates_distance() -> None:
    bus = TelemetryBus()
    received = bus.subscribe()
    state = await run_loop(SimulatedDevice(rate_hz=100.0), bus, max_ticks=20)
    assert state.distance > 0.0
    first = await anext(received)
    assert first.power is not None
    assert first.speed is not None
