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

"""Resistance controller tests against a recording fake trainer."""

from collections.abc import AsyncIterator

import pytest

from rullo.devices.base import Mode
from rullo.resistance import ResistanceController, ResistanceSettings
from rullo.telemetry import Telemetry
from rullo.workout import parse_zwo


class Recorder:
    def __init__(self) -> None:
        self.writes: list[tuple[str, float]] = []

    async def connect(self) -> None: ...

    def telemetry(self) -> AsyncIterator[Telemetry]:
        raise NotImplementedError

    async def set_grade(self, percent: float) -> None:
        self.writes.append(("grade", percent))

    async def set_target_power(self, watts: int) -> None:
        self.writes.append(("power", watts))

    async def disconnect(self) -> None: ...


async def test_sim_scales_grade_and_rate_limits() -> None:
    dev = Recorder()
    c = ResistanceController(
        dev, Mode.SIM, 250, settings=ResistanceSettings(difficulty=0.5)
    )
    await c.update(0.0, 8.0, 90)
    await c.update(0.25, 9.0, 90)  # too soon
    await c.update(1.0, 8.05, 90)  # change below the threshold
    await c.update(2.0, 10.0, 90)
    await c.update(3.0, 60.0, 90)  # capped at the steepest pitch (15%)
    assert dev.writes == [("grade", 4.0), ("grade", 5.0), ("grade", 15.0)]


async def test_free_mode_never_writes() -> None:
    dev = Recorder()
    c = ResistanceController(dev, Mode.FREE, 250)
    for t in range(5):
        await c.update(float(t), 5.0, 90)
    assert dev.writes == []


async def test_erg_ramps_toward_new_targets() -> None:
    dev = Recorder()
    zwo = (
        '<workout_file><workout><SteadyState Duration="10" Power="0.5"/>'
        '<SteadyState Duration="10" Power="1.0"/></workout></workout_file>'
    )
    c = ResistanceController(
        dev,
        Mode.ERG,
        200,
        workout=parse_zwo(zwo),
        settings=ResistanceSettings(max_ramp_w_per_s=50),
    )
    for t in range(0, 16):
        await c.update(float(t), 0.0, 90)
    powers = [w for kind, w in dev.writes if kind == "power"]
    assert powers[0] == 100  # first write jumps straight to the target
    assert powers[1:] == [150, 200]  # then ramps at 50 W per one-second write


async def test_cadence_floor_releases_and_restores_erg() -> None:
    dev = Recorder()
    s = ResistanceSettings(cadence_floor=60, release_fraction=0.5, max_ramp_w_per_s=500)
    c = ResistanceController(dev, Mode.ERG, 250, target_power=300, settings=s)
    await c.update(0.0, 0.0, 90)
    await c.update(1.0, 0.0, 50)  # rider is grinding to a halt
    assert c.released
    await c.update(2.0, 0.0, 62)  # inside the hysteresis band: still released
    assert c.released
    await c.update(3.0, 0.0, 70)
    assert not c.released
    assert dev.writes == [("power", 300), ("power", 150), ("power", 300)]


async def test_free_ride_block_falls_back_to_grade() -> None:
    dev = Recorder()
    zwo = '<workout_file><workout><FreeRide Duration="60"/></workout></workout_file>'
    c = ResistanceController(dev, Mode.ERG, 250, workout=parse_zwo(zwo))
    await c.update(0.0, 4.0, 90)
    assert dev.writes == [("grade", 2.0)]
    assert c.target is None


def test_erg_without_target_is_rejected() -> None:
    with pytest.raises(ValueError):
        ResistanceController(Recorder(), Mode.ERG, 250)


def test_descents_are_softened_and_pitches_capped() -> None:
    c = ResistanceController(
        Recorder(),
        Mode.SIM,
        250,
        settings=ResistanceSettings(difficulty=1.0, descent_scale=0.5, grade_cap=12.0),
    )
    assert c.trainer_grade(8.0) == 8.0
    assert c.trainer_grade(-8.0) == -4.0
    assert c.trainer_grade(18.0) == 12.0
    assert c.trainer_grade(-40.0) == -12.0


async def test_hold_eases_off_while_paused() -> None:
    dev = Recorder()
    erg = ResistanceController(dev, Mode.ERG, 300, target_power=280)
    await erg.update(0.0, 0.0, 90)
    await erg.hold()
    assert dev.writes == [("power", 280), ("power", 120)]  # 40% of FTP
    sim = ResistanceController(dev, Mode.SIM, 300)
    await sim.hold()
    assert dev.writes[-1] == ("grade", 0.0)
