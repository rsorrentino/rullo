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

"""FIT recorder tests, decoded with the independent ``fitdecode`` library."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitdecode
import pytest

from rullo.recorder import Point, crc16, encode_fit, ride_filename, write_fit
from rullo.session import SessionMetrics

START = datetime(2026, 10, 26, 18, 0, 0, tzinfo=UTC)


def _ride() -> tuple[list[Point], SessionMetrics]:
    metrics = SessionMetrics(ftp=250)
    points = []
    for i in range(0, 241):
        t = i / 4
        if t == 30.0:
            metrics.new_lap(t, t * 8)
        power = 150 if t < 30 else 300
        p = Point(t, power, 130 + i // 10, 90, 8.0, t * 8.0, 100.0 + t * 0.2)
        metrics.add(p.t, p.power, p.heart_rate, p.cadence, p.distance, p.elevation)
        points.append(p)
    return points, metrics


def _decode(data: bytes) -> dict[str, list[Any]]:
    frames: dict[str, list[Any]] = {}
    with fitdecode.FitReader(data, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if frame.frame_type == fitdecode.FIT_FRAME_DATA:
                frames.setdefault(frame.name, []).append(frame)
    return frames


def test_crc_known_value() -> None:
    assert crc16(b"123456789") == 0xBB3D  # CRC-16/ARC check value


def test_fit_file_decodes_with_expected_content() -> None:
    points, metrics = _ride()
    frames = _decode(encode_fit(START, points, metrics))
    assert frames["file_id"][0].get_value("type") == "activity"
    records = frames["record"]
    assert len(records) == len(points)
    first, last = records[0], records[-1]
    assert first.get_value("timestamp") == START
    assert last.get_value("power") == 300
    assert last.get_value("heart_rate") == points[-1].heart_rate
    assert last.get_value("cadence") == 90
    assert last.get_value("distance") == pytest.approx(480.0)
    assert last.get_value("speed") == pytest.approx(8.0)
    assert last.get_value("altitude") == pytest.approx(112.0, abs=0.2)
    laps = frames["lap"]
    assert len(laps) == 2
    assert laps[0].get_value("avg_power") == 150
    assert laps[1].get_value("avg_power") == 300
    session = frames["session"][0]
    assert session.get_value("sport") == "cycling"
    assert session.get_value("sub_sport") == "indoor_cycling"
    assert session.get_value("total_timer_time") == pytest.approx(60.0)
    assert session.get_value("total_distance") == pytest.approx(480.0)
    assert session.get_value("normalized_power") == metrics.normalized_power
    assert session.get_value("threshold_power") == 250
    assert session.get_value("num_laps") == 2
    assert frames["activity"][0].get_value("num_sessions") == 1
    events = [
        (e.get_value("event"), e.get_value("event_type")) for e in frames["event"]
    ]
    assert events == [("timer", "start"), ("timer", "stop_all")]


def test_missing_values_are_written_as_invalid() -> None:
    metrics = SessionMetrics(ftp=200)
    points = [
        Point(0.0, None, None, None, 0.0, 0.0, 0.0),
        Point(1.0, 100, None, None, 1.0, 1.0, 0.0),
    ]
    for p in points:
        metrics.add(p.t, p.power, p.heart_rate, p.cadence, p.distance, p.elevation)
    record = _decode(encode_fit(START, points, metrics))["record"][0]
    assert record.get_value("power") is None
    assert record.get_value("heart_rate") is None


def test_write_fit_is_atomic(tmp_path: Path) -> None:
    points, metrics = _ride()
    path = write_fit(tmp_path / "rides" / ride_filename(START), START, points, metrics)
    assert path.name == "20261026T180000Z.fit"
    assert path.read_bytes()[8:12] == b".FIT"
    assert not list(tmp_path.glob("rides/*.part"))


def test_empty_ride_is_rejected() -> None:
    with pytest.raises(ValueError):
        encode_fit(START, [], SessionMetrics(ftp=200))


def test_pauses_become_timer_events() -> None:
    metrics = SessionMetrics(ftp=200)
    points = []
    for t in [0.0, 1.0, 2.0, 32.0, 33.0]:
        if t == 32.0:
            metrics.pause()
        p = Point(t, 150, None, None, 5.0, t * 5, 0.0)
        metrics.add(p.t, p.power, None, None, p.distance, 0.0)
        points.append(p)
    frames = _decode(encode_fit(START, points, metrics, pauses=[(2.5, 32.0)]))
    events = [
        (e.get_value("event_type"), e.get_value("timestamp")) for e in frames["event"]
    ]
    assert [kind for kind, _ in events] == ["start", "stop_all", "start", "stop_all"]
    assert (events[2][1] - events[1][1]).total_seconds() == pytest.approx(29.5, abs=1)
    session = frames["session"][0]
    assert session.get_value("total_timer_time") == pytest.approx(3.0)  # moving time
    assert session.get_value("total_elapsed_time") == pytest.approx(33.0)
