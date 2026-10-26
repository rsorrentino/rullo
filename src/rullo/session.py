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

"""Live ride metrics: averages, normalised power, IF, TSS and laps."""

import math
from dataclasses import dataclass, field

_NP_WINDOW_S = 30


@dataclass
class _Totals:
    """Running sums over a span of the ride (the whole session or one lap)."""

    start_t: float
    start_distance: float
    end_t: float = 0.0
    end_distance: float = 0.0
    time: float = 0.0  # s with samples
    energy: float = 0.0  # J
    max_power: int = 0
    hr_sum: float = 0.0
    hr_time: float = 0.0
    max_hr: int = 0
    cadence_sum: float = 0.0
    cadence_time: float = 0.0
    ascent: float = 0.0

    def add(
        self, dt: float, power: int, hr: int | None, cadence: int | None, climb: float
    ) -> None:
        self.time += dt
        self.energy += power * dt
        self.max_power = max(self.max_power, power)
        if hr is not None:
            self.hr_sum += hr * dt
            self.hr_time += dt
            self.max_hr = max(self.max_hr, hr)
        if cadence is not None:
            self.cadence_sum += cadence * dt
            self.cadence_time += dt
        self.ascent += max(climb, 0.0)

    def summary(self) -> dict[str, float | int | None]:
        return {
            "start_t": self.start_t,
            "elapsed": self.end_t - self.start_t,
            "distance": self.end_distance - self.start_distance,
            "avg_power": round(self.energy / self.time) if self.time else 0,
            "max_power": self.max_power,
            "avg_hr": round(self.hr_sum / self.hr_time) if self.hr_time else None,
            "max_hr": self.max_hr or None,
            "avg_cadence": (
                round(self.cadence_sum / self.cadence_time)
                if self.cadence_time
                else None
            ),
            "ascent": round(self.ascent, 1),
            "work_kj": round(self.energy / 1000.0),
        }


@dataclass
class SessionMetrics:
    """Accumulates ride metrics tick by tick; laps split the same stream."""

    ftp: int
    total: _Totals = field(default_factory=lambda: _Totals(0.0, 0.0))
    laps: list[_Totals] = field(default_factory=lambda: [_Totals(0.0, 0.0)])
    _prev_t: float | None = None
    _prev_elevation: float | None = None
    # Normalised power works on 1 s buckets and their 30 s rolling mean.
    _bucket_second: int = 0
    _bucket_energy: float = 0.0
    _bucket_time: float = 0.0
    _seconds: list[float] = field(default_factory=list)
    _window_sum: float = 0.0
    _fourth_sum: float = 0.0
    _fourth_count: int = 0

    def add(
        self,
        t: float,
        power: int | None,
        heart_rate: int | None,
        cadence: int | None,
        distance: float,
        elevation: float,
    ) -> None:
        dt = 0.0 if self._prev_t is None else t - self._prev_t
        climb = (
            0.0 if self._prev_elevation is None else elevation - self._prev_elevation
        )
        self._prev_t, self._prev_elevation = t, elevation
        watts = power or 0
        for span in (self.total, self.laps[-1]):
            span.add(dt, watts, heart_rate, cadence, climb)
            span.end_t, span.end_distance = t, distance
        self._add_np(t, watts, dt)

    def new_lap(self, t: float, distance: float) -> None:
        if self.laps[-1].time > 0:
            self.laps.append(_Totals(t, distance, t, distance))

    def _add_np(self, t: float, watts: int, dt: float) -> None:
        second = int(t)
        while second > self._bucket_second:
            mean = self._bucket_energy / self._bucket_time if self._bucket_time else 0.0
            self._push_second(mean if self._bucket_time else watts)
            self._bucket_second += 1
            self._bucket_energy = self._bucket_time = 0.0
        self._bucket_energy += watts * dt
        self._bucket_time += dt

    def _push_second(self, mean: float) -> None:
        self._seconds.append(mean)
        self._window_sum += mean
        if len(self._seconds) > _NP_WINDOW_S:
            self._window_sum -= self._seconds[-_NP_WINDOW_S - 1]
        if len(self._seconds) >= _NP_WINDOW_S:
            self._fourth_sum += (self._window_sum / _NP_WINDOW_S) ** 4
            self._fourth_count += 1

    @property
    def normalized_power(self) -> int:
        if not self._fourth_count:  # under 30 s: fall back to the average
            return round(self.total.energy / self.total.time) if self.total.time else 0
        return round(math.sqrt(math.sqrt(self._fourth_sum / self._fourth_count)))

    @property
    def intensity_factor(self) -> float:
        return self.normalized_power / self.ftp if self.ftp else 0.0

    @property
    def tss(self) -> float:
        return self.total.time / 3600.0 * self.intensity_factor**2 * 100.0

    def summary(self) -> dict[str, object]:
        return self.total.summary() | {
            "normalized_power": self.normalized_power,
            "intensity_factor": round(self.intensity_factor, 2),
            "tss": round(self.tss),
            "ftp": self.ftp,
            "laps": [lap.summary() for lap in self.laps if lap.time > 0],
        }
