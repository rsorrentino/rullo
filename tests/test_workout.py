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

"""Workout parsing and ERG target tests."""

import pytest

from rullo.workout import parse_erg, parse_zwo, to_zwo, zone_of

ZWO = """<workout_file><name>Test Intervals</name><description>d</description>
<workout>
  <Warmup Duration="100" PowerLow="0.5" PowerHigh="0.7"/>
  <IntervalsT Repeat="2" OnDuration="60" OffDuration="30" OnPower="1.2" OffPower="0.5"/>
  <FreeRide Duration="50"/>
  <SteadyState Duration="40" Power="0.8"/>
  <textevent timeoffset="10" message="go"/>
</workout></workout_file>"""


def test_parses_and_expands_intervals() -> None:
    w = parse_zwo(ZWO)
    assert w.name == "Test Intervals"
    labels = [s.label for s in w.steps]
    assert labels == [
        "Warmup",
        "Interval 1/2",
        "Recovery 1/2",
        "Interval 2/2",
        "Recovery 2/2",
        "Free ride",
        "Steady",
    ]
    assert w.duration == 100 + 2 * 90 + 50 + 40


def test_targets_over_time() -> None:
    w = parse_zwo(ZWO)
    assert w.target_at(0, 200) == 100  # ramp starts at 50%
    assert w.target_at(50, 200) == 120  # halfway up the ramp
    assert w.target_at(110, 200) == 240  # first interval at 120%
    assert w.target_at(170, 200) == 100  # recovery
    assert w.target_at(300, 200) is None  # free ride
    assert w.target_at(340, 200) == 160
    assert w.target_at(w.duration, 200) is None  # finished


def test_steady_hour_at_ftp_is_100_tss() -> None:
    w = parse_zwo(
        '<workout_file><workout><SteadyState Duration="3600" Power="1.0"/>'
        "</workout></workout_file>"
    )
    assert w.intensity_factor() == pytest.approx(1.0)
    assert w.tss() == pytest.approx(100.0)


def test_invalid_workouts_are_rejected() -> None:
    with pytest.raises(ValueError):
        parse_zwo("<workout_file><workout/></workout_file>")
    with pytest.raises(ValueError):
        parse_zwo(
            "<workout_file><workout><SteadyState Power='1'/></workout></workout_file>"
        )


def test_zones() -> None:
    assert [zone_of(f) for f in (0.5, 0.6, 0.8, 1.0, 1.1, 1.3, 2.0)] == list(
        range(1, 8)
    )


ERG = """[COURSE HEADER]
VERSION = 2
UNITS = ENGLISH
DESCRIPTION = Test ramp
FILE NAME = Watts Test
FTP = 200
MINUTES WATTS
[END COURSE HEADER]
[COURSE DATA]
0.00 100
5.00 200
5.00 300
6.00 300
[END COURSE DATA]
"""


def test_parse_erg_watts_and_mrc_percent() -> None:
    w = parse_erg(ERG, ftp=250)
    assert w.name == "Watts Test"
    assert [(s.start, s.duration, s.power_low, s.power_high) for s in w.steps] == [
        (0.0, 300.0, 0.5, 1.0),
        (300.0, 60.0, 1.5, 1.5),
    ]  # file FTP 200 wins
    mrc = ERG.replace("MINUTES WATTS", "MINUTES PERCENT").replace("FTP = 200\n", "")
    m = parse_erg(
        mrc.replace(" 100", " 50").replace(" 200", " 75").replace(" 300", " 110"), 250
    )
    assert [s.power_low for s in m.steps] == [0.5, 1.1]
    with pytest.raises(ValueError):
        parse_erg("[COURSE HEADER]\n[COURSE DATA]\n0 1\n1 1\n", 250)


def test_zwo_round_trip() -> None:
    original = parse_zwo(ZWO)
    again = parse_zwo(to_zwo(original))
    assert again.name == original.name
    assert [(s.start, s.duration, s.power_low, s.power_high) for s in again.steps] == [
        (s.start, s.duration, s.power_low, s.power_high) for s in original.steps
    ]


def test_ramp_test_tag() -> None:
    zwo = (
        "<workout_file><tags><tag name='RAMP_TEST'/></tags><workout>"
        "<SteadyState Duration='60' Power='0.5'/></workout></workout_file>"
    )
    assert parse_zwo(zwo).ramp_test and not parse_zwo(ZWO).ramp_test
