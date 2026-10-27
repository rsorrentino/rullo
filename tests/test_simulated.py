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

import asyncio
from pathlib import Path

import pytest

from rullo.__main__ import main
from rullo.bus import TelemetryBus
from rullo.devices.base import Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.loop import run_loop
from rullo.telemetry import Telemetry


async def _fast_sleep(_: float) -> None:
    # Yield to the event loop without waiting in real time.
    await asyncio.sleep(0)


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
    samples = await _collect(SimulatedDevice(sleep=_fast_sleep), 5)
    assert all(s.power is not None for s in samples)


async def test_simulated_is_deterministic_given_seed() -> None:
    samples_a = await _collect(SimulatedDevice(sleep=_fast_sleep, seed=42), 10)
    samples_b = await _collect(SimulatedDevice(sleep=_fast_sleep, seed=42), 10)
    assert [s.power for s in samples_a] == [s.power for s in samples_b]


async def test_erg_mode_converges_to_target() -> None:
    device = SimulatedDevice(mode=Mode.ERG, rider_power=100, sleep=_fast_sleep)
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
    state = await run_loop(SimulatedDevice(sleep=_fast_sleep), bus, max_ticks=20)
    assert state.distance > 0.0
    first = await anext(received)
    assert first.power is not None
    assert first.speed is not None


async def test_loop_writes_grade_and_target_to_device() -> None:
    device = SimulatedDevice(mode=Mode.ERG, sleep=_fast_sleep)
    await run_loop(device, TelemetryBus(), grade=4.2, target_power=250, max_ticks=1)
    assert device.grade == 4.2
    assert device.target_power == 250


async def test_loop_stops_at_duration() -> None:
    device = SimulatedDevice(rate_hz=4.0, sleep=_fast_sleep)
    state = await run_loop(device, TelemetryBus(), duration_s=10.0)
    assert state.elapsed == 10.0
    assert state.distance > 0.0


def test_cli_rides_a_short_session(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ride", "--duration", "0.2", "--rate", "20"]) == 0
    out = capsys.readouterr().out
    assert "P:" in out
    assert out.strip().splitlines()[-1].startswith("Done:")


def test_cli_rides_a_route(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["ride", "--route", "col-climb", "--duration", "0.3", "--rate", "20"]) == 0
    )
    assert "Done:" in capsys.readouterr().out


def test_cli_erg_requires_target() -> None:
    with pytest.raises(SystemExit):
        main(["ride", "--mode", "erg"])


def test_cli_rejects_unknown_route() -> None:
    with pytest.raises(SystemExit):
        main(["ride", "--route", "nowhere"])


def test_platform_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from rullo import config

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.platform", "darwin")
    assert config.config_dir() == tmp_path / "Library" / "Application Support" / "Rullo"
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config.config_dir() == tmp_path / "Roaming" / "Rullo"
    assert config.data_dir() == tmp_path / "Local" / "Rullo"
    monkeypatch.setattr("sys.platform", "linux")
    assert config.data_dir() == tmp_path / ".local" / "share" / "rullo"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert config.data_dir() == tmp_path / "xdg" / "rullo"


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("rullo ")
