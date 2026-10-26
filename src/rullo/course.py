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

"""Course model: elevation profile -> gradient at the rider's position.

Courses always loop. A GPX track whose ends are far apart is ridden out and
back, so the profile never jumps at the wrap-around.
"""

import math
import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

_EARTH_RADIUS_M = 6_371_000.0
_STEP_M = 10.0  # resampling resolution
_SMOOTH_M = 60.0  # moving-average window applied to elevation
_MAX_GRADE = 25.0  # %, clamps GPS elevation noise
_LOOP_GAP_M = 100.0  # ends closer than this already form a loop


@dataclass(frozen=True)
class Course:
    """A looping elevation profile sampled every few metres."""

    name: str
    distances: tuple[float, ...]  # m, strictly increasing, starts at 0
    elevations: tuple[float, ...]  # m

    @property
    def length(self) -> float:
        return self.distances[-1]

    @property
    def ascent(self) -> float:
        rises = zip(self.elevations, self.elevations[1:], strict=False)
        return sum(max(b - a, 0.0) for a, b in rises)

    def _segment(self, pos: float) -> tuple[int, float]:
        pos %= self.length
        i = min(bisect_right(self.distances, pos) - 1, len(self.distances) - 2)
        return i, pos

    def elevation_at(self, pos: float) -> float:
        i, pos = self._segment(pos)
        d0, d1 = self.distances[i], self.distances[i + 1]
        e0, e1 = self.elevations[i], self.elevations[i + 1]
        return e0 + (e1 - e0) * (pos - d0) / (d1 - d0)

    def grade_at(self, pos: float) -> float:
        """Gradient in percent at ``pos`` metres (wrapping around the loop)."""
        i, _ = self._segment(pos)
        rise = self.elevations[i + 1] - self.elevations[i]
        run = self.distances[i + 1] - self.distances[i]
        return max(-_MAX_GRADE, min(_MAX_GRADE, 100.0 * rise / run))

    def profile(self, points: int = 120) -> list[tuple[float, float]]:
        """Evenly spaced (distance, elevation) pairs, for drawing."""
        step = self.length / (points - 1)
        return [
            (i * step, self.elevation_at(min(i * step, self.length - 1e-6)))
            for i in range(points)
        ]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def from_points(name: str, points: list[tuple[float, float, float]]) -> Course:
    """Build a course from (lat, lon, elevation) points along a track."""
    if len(points) < 2:
        raise ValueError("a course needs at least two points")
    dist, raw = [0.0], [points[0][2]]
    for (la1, lo1, _), (la2, lo2, ele) in zip(points, points[1:], strict=False):
        step = _haversine(la1, lo1, la2, lo2)
        if step > 0.0:
            dist.append(dist[-1] + step)
            raw.append(ele)
    if dist[-1] < 2 * _STEP_M:
        raise ValueError("course is too short")
    ends_gap = _haversine(points[0][0], points[0][1], points[-1][0], points[-1][1])
    if ends_gap > _LOOP_GAP_M:  # ride it out and back
        total = dist[-1]
        dist += [2 * total - d for d in reversed(dist[:-1])]
        raw += list(reversed(raw[:-1]))
    return _resample(name, dist, raw)


def _resample(name: str, dist: list[float], ele: list[float]) -> Course:
    length = dist[-1]
    n = max(int(length // _STEP_M), 2)
    xs = [length * i / n for i in range(n + 1)]
    ys, j = [], 0
    for x in xs:
        while j < len(dist) - 2 and dist[j + 1] < x:
            j += 1
        span = dist[j + 1] - dist[j]
        frac = 0.0 if span == 0 else (x - dist[j]) / span
        ys.append(ele[j] + (ele[j + 1] - ele[j]) * min(max(frac, 0.0), 1.0))
    half = max(int(_SMOOTH_M / _STEP_M / 2), 1)
    smooth = []
    for i in range(len(ys)):
        window = [ys[(i + k) % n] for k in range(-half, half + 1)]  # wraps: loop
        smooth.append(sum(window) / len(window))
    smooth[-1] = smooth[0]  # the loop closes exactly
    return Course(name, tuple(xs), tuple(smooth))


def parse_gpx(text: str, fallback_name: str = "Imported route") -> Course:
    """Parse track points (or route points) from GPX 1.0/1.1 text."""
    root = ET.fromstring(text)
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    pts = root.findall(f".//{ns}trkpt") or root.findall(f".//{ns}rtept")
    points = []
    for p in pts:
        ele = p.find(f"{ns}ele")
        points.append(
            (
                float(p.attrib["lat"]),
                float(p.attrib["lon"]),
                float(ele.text) if ele is not None and ele.text else 0.0,
            )
        )
    name_el = root.find(f".//{ns}trk/{ns}name")
    if name_el is None:
        name_el = root.find(f".//{ns}metadata/{ns}name")
    name = name_el.text.strip() if name_el is not None and name_el.text else ""
    return from_points(name or fallback_name, points)


def load_gpx(path: Path) -> Course:
    return parse_gpx(path.read_text(encoding="utf-8"), path.stem.replace("-", " "))
