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

"""Deterministic cycling physics: power in, speed out."""

import math
from dataclasses import dataclass

G = 9.80665


@dataclass(frozen=True)
class RiderParams:
    """Rider plus bike parameters."""

    mass_kg: float = 78.0  # kg, rider + bike
    crr: float = 0.004  # rolling resistance coefficient
    cda: float = 0.32  # drag area, m^2
    rho: float = 1.225  # air density, kg/m^3
    drive_loss: float = 0.02  # fraction of power lost in the drivetrain


def speed_from_power(
    power: float, grade: float, v_prev: float, params: RiderParams, dt: float
) -> float:
    """Advance speed (m/s) by ``dt`` seconds given ``power`` W and ``grade`` %.

    Uses the power equation ``F = P_wheel / v`` against gravity, rolling and
    aerodynamic resistance, integrated with semi-implicit Euler. Never negative.
    """
    theta = math.atan(grade / 100.0)
    p_wheel = max(power, 0.0) * (1.0 - params.drive_loss)
    v = max(v_prev, 0.0)
    f_static = params.mass_kg * G * (math.sin(theta) + params.crr * math.cos(theta))
    # Avoid the 1/v singularity at standstill: cap driving force from a low speed.
    f_drive = p_wheel / max(v, 0.5)
    f_drag = 0.5 * params.rho * params.cda * v * v
    accel = (f_drive - f_static - f_drag) / params.mass_kg
    return max(v + accel * dt, 0.0)
