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

"""Course model tests."""

from pathlib import Path

import pytest

from rullo.course import parse_gpx
from rullo.library import Library


def _gpx(points: list[tuple[float, float, float]], name: str = "Test") -> str:
    pts = "".join(
        f'<trkpt lat="{la}" lon="{lo}"><ele>{e}</ele></trkpt>' for la, lo, e in points
    )
    return (
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name><trkseg>{pts}</trkseg></trk></gpx>"
    )


def _climb(length_m: float = 2000.0, rise_m: float = 100.0) -> str:
    n = 40
    deg = length_m / 111_320
    return _gpx([(45.0 + deg * i / n, 9.0, rise_m * i / n) for i in range(n + 1)])


def test_out_and_back_climb_has_matching_grades() -> None:
    course = parse_gpx(_climb())
    assert course.name == "Test"
    assert course.length == pytest.approx(4000.0, rel=0.01)  # ridden out and back
    assert course.grade_at(1000.0) == pytest.approx(5.0, abs=0.3)
    assert course.grade_at(3000.0) == pytest.approx(-5.0, abs=0.3)
    assert course.elevation_at(2000.0) == pytest.approx(100.0, abs=3.0)


def test_course_wraps_around_without_a_jump() -> None:
    course = parse_gpx(_climb())
    end, start = course.elevation_at(course.length - 0.01), course.elevation_at(0.0)
    assert end == pytest.approx(start, abs=0.5)
    assert course.grade_at(course.length + 1000.0) == course.grade_at(1000.0)


def test_route_points_and_missing_elevation_are_accepted() -> None:
    deg = 1000 / 111_320
    text = (
        '<gpx version="1.0"><rte>'
        f'<rtept lat="45.0" lon="9.0"/><rtept lat="{45.0 + deg}" lon="9.0"/>'
        "</rte></gpx>"
    )
    course = parse_gpx(text, "Fallback")
    assert course.name == "Fallback"
    assert course.grade_at(500.0) == 0.0


def test_too_few_points_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse_gpx(_gpx([(45.0, 9.0, 0.0)]))


def test_bundled_routes_load(tmp_path: Path) -> None:
    routes = Library(tmp_path).routes()
    assert {"alpine-loop", "coastal-rollers", "col-climb"} <= routes.keys()
    alpine = routes["alpine-loop"]
    assert 5.0 < max(alpine.grade_at(d) for d in range(0, int(alpine.length), 20))
    assert alpine.ascent > 200
