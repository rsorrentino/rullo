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

"""TCX export: Garmin Training Center XML, for tools that do not read .fit."""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
_TPX = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"


def ride_to_tcx(ride: dict[str, Any]) -> bytes:
    """Build a TCX document from a saved ride (summary plus its 1 s trace)."""
    ET.register_namespace("", _NS)
    ET.register_namespace("tpx", _TPX)
    start = datetime.fromisoformat(ride["started_at"])
    summary = ride["summary"]

    def iso(t: float) -> str:
        return (start + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def sub(
        parent: ET.Element, tag: str, text: object = None, ns: str = _NS
    ) -> ET.Element:
        el = ET.SubElement(parent, f"{{{ns}}}{tag}")
        if text is not None:
            el.text = str(text)
        return el

    root = ET.Element(f"{{{_NS}}}TrainingCenterDatabase")
    activity = sub(sub(root, "Activities"), "Activity")
    activity.set("Sport", "Biking")
    sub(activity, "Id", iso(0))
    trace = ride.get("trace") or []
    laps = summary.get("laps") or [summary | {"start_t": 0.0}]
    for i, lap in enumerate(laps):
        lap_start = float(lap.get("start_t", 0.0))
        lap_end = float(laps[i + 1]["start_t"]) if i + 1 < len(laps) else float("inf")
        el = sub(activity, "Lap")
        el.set("StartTime", iso(lap_start))
        sub(el, "TotalTimeSeconds", round(float(lap["elapsed"]), 1))
        sub(el, "DistanceMeters", round(float(lap["distance"]), 1))
        sub(el, "Calories", 0)
        if lap.get("avg_hr"):
            sub(sub(el, "AverageHeartRateBpm"), "Value", lap["avg_hr"])
        if lap.get("max_hr"):
            sub(sub(el, "MaximumHeartRateBpm"), "Value", lap["max_hr"])
        sub(el, "Intensity", "Active")
        if lap.get("avg_cadence"):
            sub(el, "Cadence", lap["avg_cadence"])
        sub(el, "TriggerMethod", "Manual")
        track = sub(el, "Track")
        for row in trace:
            t, power, hr, cadence, speed = row[:5]
            distance = row[6] if len(row) > 6 else None
            if not lap_start <= t < lap_end:
                continue
            point = sub(track, "Trackpoint")
            sub(point, "Time", iso(t))
            if distance is not None:
                sub(point, "DistanceMeters", round(float(distance), 1))
            if hr:
                sub(sub(point, "HeartRateBpm"), "Value", hr)
            if cadence is not None:
                sub(point, "Cadence", cadence)
            tpx = sub(sub(point, "Extensions"), "TPX", ns=_TPX)
            sub(tpx, "Speed", round(float(speed or 0), 2), ns=_TPX)
            if power is not None:
                sub(tpx, "Watts", power, ns=_TPX)
    document: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return document
