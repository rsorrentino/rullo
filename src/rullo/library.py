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

"""Library: bundled and imported routes and workouts, and saved rides."""

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from rullo.analytics import best_powers, trace_powers
from rullo.course import Course, parse_gpx
from rullo.workout import Workout, parse_erg, parse_zwo, to_zwo

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-") or "item"


class Library:
    """Routes and workouts from the package plus the user's data directory."""

    def __init__(self, data: Path) -> None:
        self.data = data
        self.rides_dir = data / "rides"
        self._routes: dict[str, Course] | None = None
        self._workouts: dict[str, Workout] | None = None

    def _sources(self, kind: str, suffix: str) -> list[tuple[str, str]]:
        found: dict[str, str] = {}
        bundled = files("rullo") / "data" / kind
        for item in sorted(bundled.iterdir(), key=lambda i: i.name):
            if item.name.endswith(suffix):
                found[item.name.removesuffix(suffix)] = item.read_text(encoding="utf-8")
        user = self.data / kind
        if user.is_dir():  # user files override bundled ones with the same id
            for path in sorted(user.glob(f"*{suffix}")):
                found[path.stem] = path.read_text(encoding="utf-8")
        return list(found.items())

    def routes(self) -> dict[str, Course]:
        if self._routes is None:
            self._routes = {}
            for key, text in self._sources("routes", ".gpx"):
                try:
                    self._routes[key] = parse_gpx(text, key.replace("-", " ").title())
                except (ValueError, KeyError, SyntaxError):
                    continue  # one bad file should not hide the rest
        return self._routes

    def workouts(self) -> dict[str, Workout]:
        if self._workouts is None:
            self._workouts = {}
            for key, text in self._sources("workouts", ".zwo"):
                try:
                    self._workouts[key] = parse_zwo(text, key.replace("-", " ").title())
                except (ValueError, SyntaxError):
                    continue
        return self._workouts

    def import_route(self, filename: str, data: bytes) -> str:
        text = data.decode("utf-8")
        course = parse_gpx(text, Path(filename).stem)  # validates before saving
        key = slug(course.name)
        self._save("routes", key + ".gpx", text)
        self._routes = None
        return key

    def import_workout(self, filename: str, data: bytes, ftp: int = 250) -> str:
        """Import a .zwo, or an .erg/.mrc converted to .zwo (watts use ``ftp``)."""
        text = data.decode("utf-8-sig")
        stem = Path(filename).stem
        if filename.lower().endswith((".erg", ".mrc")):
            workout = parse_erg(text, ftp, stem)
            text = to_zwo(workout)
        else:
            workout = parse_zwo(text, stem)
        key = slug(workout.name)
        self._save("workouts", key + ".zwo", text)
        self._workouts = None
        return key

    def _save(self, kind: str, name: str, text: str) -> None:
        folder = self.data / kind
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")

    def rides(self) -> list[dict[str, Any]]:
        """Saved ride summaries, newest first (without the trace)."""
        out = []
        for path in sorted(self.rides_dir.glob("*.json"), reverse=True):
            try:
                ride = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            trace = ride.pop("trace", None) or []
            if "best_powers" not in ride:  # rides saved before the power curve
                ride["best_powers"] = best_powers(trace_powers(trace))
            out.append(ride)
        return out

    def ride(self, ride_id: str) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            self.ride_path(ride_id, ".json").read_text()
        )
        return result

    def ride_path(self, ride_id: str, suffix: str) -> Path:
        if not re.fullmatch(r"[0-9TZ]+", ride_id):
            raise KeyError(ride_id)
        path = self.rides_dir / (ride_id + suffix)
        if not path.exists():
            raise KeyError(ride_id)
        return path

    def delete_ride(self, ride_id: str) -> None:
        """Remove a saved ride: its .fit file and its summary."""
        summary = self.ride_path(ride_id, ".json")  # raises KeyError if unknown
        fit = self.rides_dir / (ride_id + ".fit")
        fit.unlink(missing_ok=True)
        summary.unlink()

    def save_ride(self, ride_id: str, ride: dict[str, Any]) -> None:
        self.rides_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.rides_dir / (ride_id + ".json.part")
        tmp.write_text(json.dumps(ride), encoding="utf-8")
        tmp.replace(self.rides_dir / (ride_id + ".json"))


def route_info(key: str, course: Course) -> dict[str, Any]:
    return {
        "id": key,
        "name": course.name,
        "length": round(course.length),
        "ascent": round(course.ascent),
        "profile": [[round(d), round(e, 1)] for d, e in course.profile(120)],
    }


def workout_info(key: str, workout: Workout) -> dict[str, Any]:
    return {
        "id": key,
        "name": workout.name,
        "description": workout.description,
        "duration": workout.duration,
        "tss": round(workout.tss()),
        "intensity_factor": round(workout.intensity_factor(), 2),
        "ramp_test": workout.ramp_test,
        "steps": [
            {
                "start": s.start,
                "duration": s.duration,
                "low": s.power_low,
                "high": s.power_high,
                "label": s.label,
            }
            for s in workout.steps
        ],
    }
