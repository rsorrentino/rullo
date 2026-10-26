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

"""Ride recorder: buffers samples and writes a FIT activity file.

The ``.fit`` file is the source of truth for a ride; uploads read it back.
This is a small, dependency-free encoder for the messages Strava and
intervals.icu need: file_id, timer events, records, laps, session, activity.
"""

import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rullo.session import SessionMetrics

_FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z as a Unix timestamp
_CRC_TABLE = (
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
)

# Base types: (code, struct format, invalid value).
_ENUM = (0x00, "B", 0xFF)
_UINT8 = (0x02, "B", 0xFF)
_UINT16 = (0x84, "H", 0xFFFF)
_UINT32 = (0x86, "I", 0xFFFFFFFF)
_UINT32Z = (0x8C, "I", 0)

_Field = tuple[int, tuple[int, str, int]]  # (field number, base type)

# Global message numbers and the fields we write, in order.
_FILE_ID = (0, [(0, _ENUM), (1, _UINT16), (2, _UINT16), (3, _UINT32Z), (4, _UINT32)])
_EVENT = (21, [(253, _UINT32), (0, _ENUM), (1, _ENUM)])
_RECORD = (
    20,
    [
        (253, _UINT32),
        (2, _UINT16),
        (3, _UINT8),
        (4, _UINT8),
        (5, _UINT32),
        (6, _UINT16),
        (7, _UINT16),
    ],
)
_LAP = (
    19,
    [
        (253, _UINT32),
        (254, _UINT16),
        (0, _ENUM),
        (1, _ENUM),
        (2, _UINT32),
        (7, _UINT32),
        (8, _UINT32),
        (9, _UINT32),
        (15, _UINT8),
        (16, _UINT8),
        (17, _UINT8),
        (19, _UINT16),
        (20, _UINT16),
        (21, _UINT16),
        (25, _ENUM),
    ],
)
_SESSION = (
    18,
    [
        (253, _UINT32),
        (254, _UINT16),
        (0, _ENUM),
        (1, _ENUM),
        (2, _UINT32),
        (5, _ENUM),
        (6, _ENUM),
        (7, _UINT32),
        (8, _UINT32),
        (9, _UINT32),
        (16, _UINT8),
        (17, _UINT8),
        (18, _UINT8),
        (20, _UINT16),
        (21, _UINT16),
        (22, _UINT16),
        (25, _UINT16),
        (26, _UINT16),
        (34, _UINT16),
        (35, _UINT16),
        (36, _UINT16),
        (45, _UINT16),
    ],
)
_ACTIVITY = (
    34,
    [
        (253, _UINT32),
        (0, _UINT32),
        (1, _UINT16),
        (2, _ENUM),
        (3, _ENUM),
        (4, _ENUM),
        (5, _UINT32),
    ],
)

_SPORT_CYCLING, _SUB_SPORT_INDOOR = 2, 6
_EVENT_TIMER, _EVENT_LAP, _EVENT_SESSION, _EVENT_ACTIVITY = 0, 9, 8, 26
_TYPE_START, _TYPE_STOP, _TYPE_STOP_ALL = 0, 1, 4
_MANUFACTURER_DEVELOPMENT = 255


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ _CRC_TABLE[nibble]
    return crc


@dataclass(frozen=True)
class Point:
    t: float  # s since ride start
    power: int | None
    heart_rate: int | None
    cadence: int | None
    speed: float  # m/s
    distance: float  # m
    elevation: float  # m


class _Encoder:
    def __init__(self) -> None:
        self._body = bytearray()
        self._local: dict[int, int] = {}

    def write(
        self, message: tuple[int, list[_Field]], values: list[float | int | None]
    ) -> None:
        number, fields = message
        if number not in self._local:
            local = len(self._local)
            self._local[number] = local
            self._body += struct.pack("<BBBHB", 0x40 | local, 0, 0, number, len(fields))
            for field_num, (base, fmt, _) in fields:
                self._body += struct.pack("<BBB", field_num, struct.calcsize(fmt), base)
        self._body.append(self._local[number])
        for (_, (_, fmt, invalid)), value in zip(fields, values, strict=True):
            raw = invalid if value is None else int(round(value))
            limit = (1 << (8 * struct.calcsize(fmt))) - 1
            if not 0 <= raw <= limit:
                raw = invalid  # out of range is written as "no value"
            self._body += struct.pack("<" + fmt, raw)

    def finish(self) -> bytes:
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self._body), b".FIT")
        header += struct.pack("<H", crc16(header))
        data = header + bytes(self._body)
        return data + struct.pack("<H", crc16(data))


def _fit_time(start: datetime, t: float) -> int:
    return int(start.timestamp() + t) - _FIT_EPOCH


def encode_fit(start: datetime, points: list[Point], metrics: SessionMetrics) -> bytes:
    """Encode a whole ride as FIT activity bytes."""
    if not points:
        raise ValueError("cannot record an empty ride")
    enc = _Encoder()
    ts0, ts_end = _fit_time(start, 0.0), _fit_time(start, points[-1].t)
    enc.write(_FILE_ID, [4, _MANUFACTURER_DEVELOPMENT, 0, 1, ts0])
    enc.write(_EVENT, [ts0, _EVENT_TIMER, _TYPE_START])
    for p in points:
        enc.write(
            _RECORD,
            [
                _fit_time(start, p.t),
                (p.elevation + 500.0) * 5.0,
                p.heart_rate,
                p.cadence,
                p.distance * 100.0,
                p.speed * 1000.0,
                p.power,
            ],
        )
    enc.write(_EVENT, [ts_end, _EVENT_TIMER, _TYPE_STOP_ALL])
    laps = [lap for lap in metrics.laps if lap.time > 0] or [metrics.total]
    for i, lap in enumerate(laps):
        s = lap.summary()
        enc.write(
            _LAP,
            [
                _fit_time(start, lap.end_t),
                i,
                _EVENT_LAP,
                _TYPE_STOP,
                _fit_time(start, lap.start_t),
                (lap.end_t - lap.start_t) * 1000.0,
                lap.time * 1000.0,
                (lap.end_distance - lap.start_distance) * 100.0,
                s["avg_hr"],
                s["max_hr"],
                s["avg_cadence"],
                s["avg_power"],
                s["max_power"],
                lap.ascent,
                _SPORT_CYCLING,
            ],
        )
    total = metrics.total
    s = total.summary()
    enc.write(
        _SESSION,
        [
            ts_end,
            0,
            _EVENT_SESSION,
            _TYPE_STOP,
            ts0,
            _SPORT_CYCLING,
            _SUB_SPORT_INDOOR,
            points[-1].t * 1000.0,
            total.time * 1000.0,
            points[-1].distance * 100.0,
            s["avg_hr"],
            s["max_hr"],
            s["avg_cadence"],
            s["avg_power"],
            s["max_power"],
            total.ascent,
            0,
            len(laps),
            metrics.normalized_power,
            metrics.tss * 10.0,
            metrics.intensity_factor * 1000.0,
            metrics.ftp,
        ],
    )
    local_offset = start.astimezone().utcoffset()
    offset_s = int(local_offset.total_seconds()) if local_offset else 0
    enc.write(
        _ACTIVITY,
        [
            ts_end,
            total.time * 1000.0,
            1,
            0,
            _EVENT_ACTIVITY,
            _TYPE_STOP,
            ts_end + offset_s,
        ],
    )
    return enc.finish()


def write_fit(
    path: Path, start: datetime, points: list[Point], metrics: SessionMetrics
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".fit.part")
    tmp.write_bytes(encode_fit(start, points, metrics))
    tmp.replace(path)  # never leave a half-written ride behind
    return path


def ride_filename(start: datetime) -> str:
    return start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ") + ".fit"
