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

"""Analytics tests: power curve, FTP estimates and training load."""

from datetime import date, timedelta

import pytest

from rullo.analytics import (
    best_powers,
    ftp_estimate,
    power_curve,
    ramp_test_ftp,
    training_load,
)


def test_best_powers() -> None:
    powers = [100.0] * 600 + [400.0] * 30 + [100.0] * 600
    best = best_powers(powers)
    assert best["5"] == 400 and best["30"] == 400
    assert best["60"] == 250  # 30 s at 400 W and 30 s at 100 W
    assert "3600" not in best  # the ride is shorter than an hour


def test_ftp_estimates() -> None:
    assert ramp_test_ftp([200.0] * 60 + [380.0] * 60) == 285  # 75% of 380
    assert ramp_test_ftp([]) is None
    assert ftp_estimate({"1200": 300}) == 285
    assert ftp_estimate({"60": 400}) is None


def test_power_curve_across_rides() -> None:
    today = date.today()
    rides = [
        {
            "started_at": f"{today - timedelta(days=200)}T10:00:00+00:00",
            "best_powers": {"60": 500, "1200": 300},
        },
        {
            "started_at": f"{today}T10:00:00+00:00",
            "best_powers": {"60": 420, "1200": 310},
        },
    ]
    assert power_curve(rides) == {"60": 500, "1200": 310}
    assert power_curve(rides, since=today - timedelta(days=90)) == {
        "60": 420,
        "1200": 310,
    }


def test_training_load() -> None:
    today = date(2026, 10, 26)
    rides = [
        {"started_at": f"{today - timedelta(days=d)}T09:00:00", "summary": {"tss": 100}}
        for d in range(0, 60)
    ]
    load = training_load(rides, days=60, today=today)
    assert len(load) == 60 and load[-1]["date"] == "2026-10-26"
    last = load[-1]
    assert last["atl"] > last["ctl"] > 50  # fatigue reacts faster than fitness
    assert last["tsb"] == pytest.approx(load[-2]["ctl"] - load[-2]["atl"], abs=0.2)
    assert training_load([], today=today) == []
