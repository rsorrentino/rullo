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

"""Session metrics tests."""

from collections.abc import Callable

import pytest

from rullo.session import SessionMetrics


def _ride(
    metrics: SessionMetrics,
    seconds: int,
    power: int | Callable[[float], int],
    hz: int = 4,
) -> None:
    for i in range(seconds * hz + 1):
        t = i / hz
        watts = power(t) if callable(power) else power
        metrics.add(t, watts, 140, 90, t * 10.0, t * 0.1)


def test_steady_power() -> None:
    m = SessionMetrics(ftp=200)
    _ride(m, 3600, 200)
    s = m.summary()
    assert s["avg_power"] == 200
    assert m.normalized_power == 200
    assert m.intensity_factor == pytest.approx(1.0)
    assert m.tss == pytest.approx(100.0, abs=0.5)
    assert s["avg_hr"] == 140 and s["avg_cadence"] == 90
    assert s["work_kj"] == 720
    assert s["ascent"] == pytest.approx(360.0, abs=0.5)


def test_variable_power_raises_np_above_average() -> None:
    m = SessionMetrics(ftp=250)
    _ride(m, 1200, lambda t: 400 if int(t) // 60 % 2 == 0 else 100)
    assert m.summary()["avg_power"] == pytest.approx(250, abs=2)
    assert m.normalized_power > 290


def test_short_ride_falls_back_to_average() -> None:
    m = SessionMetrics(ftp=250)
    _ride(m, 10, 180)
    assert m.normalized_power == 180


def test_laps_split_the_ride() -> None:
    m = SessionMetrics(ftp=250)
    for i in range(41):
        t = i / 4
        if t == 5.0:
            m.new_lap(t, t * 10)
        m.add(t, 100 if t < 5 else 300, None, None, t * 10, 0.0)
    laps = m.summary()["laps"]
    assert isinstance(laps, list) and len(laps) == 2
    assert laps[0]["avg_power"] == 100
    assert laps[1]["avg_power"] == 300
    assert laps[1]["distance"] == pytest.approx(50.0)
    assert laps[0]["avg_hr"] is None
    assert laps[1]["normalized_power"] == 300  # short laps fall back to the average


def test_lap_np_and_calories() -> None:
    m = SessionMetrics(ftp=250)
    _ride(m, 120, lambda t: 300 if t < 60 else 150)
    m2 = SessionMetrics(ftp=250)
    for i in range(0, 481):
        t = i / 4
        if t == 60.0:
            m2.new_lap(t, 0.0)
        m2.add(t, 300 if t < 60 else 150, None, None, 0.0, 0.0)
    laps = m2.summary()["laps"]
    assert isinstance(laps, list)
    assert laps[0]["normalized_power"] == 300
    assert laps[1]["normalized_power"] == 150
    s = m.summary()
    assert s["calories"] == round(s["work_kj"] / 4.184 / 0.24)  # type: ignore[operator]
