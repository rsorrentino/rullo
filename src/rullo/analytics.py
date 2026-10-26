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

"""Training analytics across rides: power curve, FTP estimate, training load."""

import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

# Durations (s) of the power curve, from sprint to hour power.
CURVE_S = (5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)
CTL_DAYS, ATL_DAYS = 42, 7  # fitness and fatigue time constants


def best_powers(
    powers: Sequence[float], durations: Sequence[int] = CURVE_S
) -> dict[str, int]:
    """Best average power for each duration, from power sampled once a second."""
    best: dict[str, int] = {}
    for d in durations:
        if d > len(powers):
            break
        window = sum(powers[:d])
        top = window
        for i in range(d, len(powers)):
            window += powers[i] - powers[i - d]
            top = max(top, window)
        best[str(d)] = round(top / d)
    return best


def trace_powers(trace: Sequence[Sequence[Any]]) -> list[float]:
    """Power once a second from a saved ride trace ([t, power, ...] rows)."""
    return [float(row[1] or 0) for row in trace]


def ramp_test_ftp(powers: Sequence[float]) -> int | None:
    """FTP from a ramp test: 75% of the best one-minute power."""
    best = best_powers(powers, (60,)).get("60")
    return round(best * 0.75) if best else None


def power_curve(
    rides: Sequence[dict[str, Any]], since: date | None = None
) -> dict[str, int]:
    """Best power per duration across rides (optionally only since a date)."""
    curve: dict[str, int] = {}
    for ride in rides:
        if since and _day(ride) < since:
            continue
        for d, watts in (ride.get("best_powers") or {}).items():
            curve[d] = max(curve.get(d, 0), int(watts))
    return dict(sorted(curve.items(), key=lambda kv: int(kv[0])))


def ftp_estimate(curve: dict[str, int]) -> int | None:
    """95% of the best 20-minute power, the standard field-test estimate."""
    twenty = curve.get("1200")
    return round(twenty * 0.95) if twenty else None


def training_load(
    rides: Sequence[dict[str, Any]], days: int = 120, today: date | None = None
) -> list[dict[str, Any]]:
    """Daily fitness (CTL), fatigue (ATL) and form (TSB) from ride TSS."""
    today = today or date.today()
    daily: dict[date, float] = {}
    for ride in rides:
        daily[_day(ride)] = daily.get(_day(ride), 0.0) + float(
            ride["summary"].get("tss") or 0
        )
    if not daily:
        return []
    first = min(min(daily), today - timedelta(days=days))
    ctl = atl = 0.0
    k_ctl, k_atl = 1 - math.exp(-1 / CTL_DAYS), 1 - math.exp(-1 / ATL_DAYS)
    out = []
    day = first
    while day <= today:
        form = ctl - atl  # form uses yesterday's fitness and fatigue
        tss = daily.get(day, 0.0)
        ctl += (tss - ctl) * k_ctl
        atl += (tss - atl) * k_atl
        if day > today - timedelta(days=days):
            out.append(
                {
                    "date": day.isoformat(),
                    "tss": round(tss),
                    "ctl": round(ctl, 1),
                    "atl": round(atl, 1),
                    "tsb": round(form, 1),
                }
            )
        day += timedelta(days=1)
    return out


def _day(ride: dict[str, Any]) -> date:
    return datetime.fromisoformat(ride["started_at"]).astimezone().date()
