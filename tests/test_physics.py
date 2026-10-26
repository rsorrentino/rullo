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

"""Physics tests."""

from rullo.physics import RiderParams, speed_from_power


def test_flat_steady_power_converges() -> None:
    params = RiderParams()
    v = 0.0
    for _ in range(6000):
        v = speed_from_power(200.0, 0.0, v, params, 0.1)
    assert 8.0 < v < 11.0  # ~30-40 km/h
    assert abs(speed_from_power(200.0, 0.0, v, params, 0.1) - v) < 1e-2


def test_zero_power_decelerates_to_zero() -> None:
    params = RiderParams()
    v = 10.0
    for _ in range(10000):
        nxt = speed_from_power(0.0, 0.0, v, params, 0.1)
        assert 0.0 <= nxt <= v
        v = nxt
    assert v == 0.0


def test_never_negative_uphill() -> None:
    assert speed_from_power(0.0, 15.0, 0.1, RiderParams(), 1.0) == 0.0
