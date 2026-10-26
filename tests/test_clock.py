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

"""Ride clock tests: pedal-to-start, pause and skip."""

from rullo.loop import RideClock


def test_clock_starts_on_the_first_pedal_stroke() -> None:
    clock = RideClock()
    assert clock.tick(0.0, 0, 0) == 0.0 and clock.state == "waiting"
    assert clock.tick(5.0, None, None) == 0.0  # still waiting
    clock.tick(6.0, 120, 80)
    assert clock.state == "riding"
    assert clock.tick(8.0, 150, 85) == 2.0


def test_pause_freezes_moving_time_and_skip_jumps_forward() -> None:
    clock = RideClock(pedal_to_start=False)
    clock.tick(0.0, 0, 0)
    clock.tick(10.0, 200, 90)
    clock.pause()
    assert clock.state == "paused"
    clock.tick(40.0, 0, 0)
    clock.resume()
    assert clock.tick(42.0, 200, 90) == 12.0  # the 30 s pause is not counted
    clock.skip_to(100.0)
    assert clock.tick(43.0, 200, 90) == 101.0
    assert clock.ridden == 13.0  # skipping moves the workout, not the rider's clock
    clock.skip_to(50.0)  # never backwards
    assert clock.moving == 101.0
