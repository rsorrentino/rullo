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

"""Structured workouts: Zwift ``.zwo`` parsing and ERG targets over time."""

import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Step:
    """One block of a workout; power is a fraction of FTP, None for free ride."""

    start: float  # s from workout start
    duration: float  # s
    power_low: float | None
    power_high: float | None
    label: str

    def fraction_at(self, t: float) -> float | None:
        if self.power_low is None or self.power_high is None:
            return None
        progress = min(max((t - self.start) / self.duration, 0.0), 1.0)
        return self.power_low + (self.power_high - self.power_low) * progress


@dataclass(frozen=True)
class Workout:
    name: str
    description: str
    steps: tuple[Step, ...]
    ramp_test: bool = False  # an FTP ramp test: ends when the rider fails

    @property
    def duration(self) -> float:
        last = self.steps[-1]
        return last.start + last.duration

    def step_index(self, t: float) -> int:
        starts = [s.start for s in self.steps]
        return max(0, min(bisect_right(starts, t) - 1, len(self.steps) - 1))

    def target_at(self, t: float, ftp: int) -> int | None:
        """ERG target in watts at ``t`` seconds, or None (free ride / finished)."""
        if t < 0 or t >= self.duration:
            return None
        fraction = self.steps[self.step_index(t)].fraction_at(t)
        return None if fraction is None else round(fraction * ftp)

    def intensity_factor(self) -> float:
        """Estimated IF: normalised power of the target trace over FTP."""
        trace = [
            self.steps[self.step_index(t)].fraction_at(t + 0.5) or 0.5
            for t in range(int(self.duration))
        ]
        if len(trace) < 30:
            return sum(trace) / max(len(trace), 1)
        window = sum(trace[:30])
        fourth = [(window / 30) ** 4]
        for i in range(30, len(trace)):
            window += trace[i] - trace[i - 30]
            fourth.append((window / 30) ** 4)
        return float((sum(fourth) / len(fourth)) ** 0.25)

    def tss(self) -> float:
        return self.duration / 3600.0 * self.intensity_factor() ** 2 * 100.0


def _num(el: ET.Element, *names: str) -> float | None:
    for name in names:
        for key in (name, name.lower()):
            if key in el.attrib:
                return float(el.attrib[key])
    return None


def parse_zwo(text: str, fallback_name: str = "Workout") -> Workout:
    """Parse a Zwift workout file. Power values are fractions of FTP."""
    root = ET.fromstring(text)
    container = root.find("workout")
    if container is None:
        raise ValueError("no <workout> element")
    steps: list[Step] = []
    t = 0.0

    def add(
        duration: float | None, low: float | None, high: float | None, label: str
    ) -> None:
        nonlocal t
        if duration is None or duration <= 0:
            raise ValueError(f"{label} step without a positive Duration")
        steps.append(Step(t, duration, low, high, label))
        t += duration

    for el in container:
        tag = el.tag
        if tag == "SteadyState":
            power = _num(el, "Power", "PowerLow")
            add(_num(el, "Duration"), power, power, "Steady")
        elif tag in ("Warmup", "Cooldown", "Ramp"):
            add(_num(el, "Duration"), _num(el, "PowerLow"), _num(el, "PowerHigh"), tag)
        elif tag == "IntervalsT":
            repeat = int(_num(el, "Repeat") or 1)
            on, off = _num(el, "OnPower"), _num(el, "OffPower")
            for i in range(repeat):
                add(_num(el, "OnDuration"), on, on, f"Interval {i + 1}/{repeat}")
                add(_num(el, "OffDuration"), off, off, f"Recovery {i + 1}/{repeat}")
        elif tag in ("FreeRide", "MaxEffort"):
            add(_num(el, "Duration"), None, None, "Free ride")
        # Text events and unknown tags carry no power target.
    if not steps:
        raise ValueError("workout has no steps")
    name = (root.findtext("name") or "").strip() or fallback_name
    description = (root.findtext("description") or "").strip()
    tags = {(t.get("name") or "").strip().upper() for t in root.iter("tag")}
    return Workout(name, description, tuple(steps), ramp_test="RAMP_TEST" in tags)


def to_zwo(workout: Workout) -> str:
    """Serialise a workout as .zwo text (used to store imported .erg/.mrc files)."""
    blocks = []
    for s in workout.steps:
        if s.power_low is None or s.power_high is None:
            blocks.append(f'<FreeRide Duration="{s.duration:g}"/>')
        elif s.power_low == s.power_high:
            blocks.append(
                f'<SteadyState Duration="{s.duration:g}" Power="{s.power_low:.4f}"/>'
            )
        else:
            blocks.append(
                f'<Ramp Duration="{s.duration:g}" PowerLow="{s.power_low:.4f}" '
                f'PowerHigh="{s.power_high:.4f}"/>'
            )
    root = ET.Element("workout_file")
    ET.SubElement(root, "name").text = workout.name
    ET.SubElement(root, "description").text = workout.description
    ET.SubElement(root, "sportType").text = "bike"
    body = "\n    ".join(blocks)
    head = ET.tostring(root, encoding="unicode").removesuffix("</workout_file>")
    return f"{head}<workout>\n    {body}\n</workout></workout_file>\n"


def parse_erg(text: str, ftp: int, fallback_name: str = "Workout") -> Workout:
    """Parse an .erg (absolute watts) or .mrc (percent of FTP) workout.

    Both list (minutes, value) points of a piecewise-linear target; two points
    at the same time make a step. ``ftp`` converts .erg watts to FTP fractions
    unless the file declares its own ``FTP =``.
    """
    header: dict[str, str] = {}
    points: list[tuple[float, float]] = []
    section = ""
    unit = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            section = line.upper()
            continue
        if section == "[COURSE HEADER]":
            if "=" in line:
                key, _, value = line.partition("=")
                header[key.strip().upper()] = value.strip()
            else:
                unit = line.upper()
        elif section == "[COURSE DATA]":
            fields = line.split()
            if len(fields) >= 2:
                points.append((float(fields[0]) * 60.0, float(fields[1])))
    if "PERCENT" in unit:
        scale = 0.01
    elif "WATTS" in unit:
        base = float(header.get("FTP") or ftp)
        if base <= 0:
            raise ValueError("an .erg workout needs an FTP to convert watts")
        scale = 1.0 / base
    else:
        raise ValueError("expected a MINUTES WATTS or MINUTES PERCENT header")
    steps: list[Step] = []
    for (t0, v0), (t1, v1) in zip(points, points[1:], strict=False):
        if t1 > t0:
            steps.append(
                Step(
                    t0,
                    t1 - t0,
                    v0 * scale,
                    v1 * scale,
                    "Steady" if v0 == v1 else "Ramp",
                )
            )
    if not steps:
        raise ValueError("workout has no steps")
    name = header.get("FILE NAME") or header.get("DESCRIPTION") or fallback_name
    return Workout(name.strip(), header.get("DESCRIPTION", "").strip(), tuple(steps))


def load_zwo(path: Path) -> Workout:
    return parse_zwo(path.read_text(encoding="utf-8"), path.stem.replace("-", " "))


def zone_of(fraction: float) -> int:
    """Coggan power zone (1-7) for a fraction of FTP."""
    for zone, upper in enumerate((0.55, 0.75, 0.90, 1.05, 1.20, 1.50), start=1):
        if fraction < upper:
            return zone
    return 7
