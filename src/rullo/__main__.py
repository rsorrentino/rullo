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

"""Command-line entry point: ride a session against the simulated trainer."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from rullo.devices.base import Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.loop import run
from rullo.physics import RiderParams


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rullo",
        description="Ride a headless session against the simulated trainer.",
    )
    parser.add_argument(
        "--mode", choices=[m.value for m in Mode], default=Mode.SIM.value
    )
    parser.add_argument("--duration", type=float, default=60.0, help="seconds")
    parser.add_argument("--grade", type=float, default=0.0, help="course grade, %%")
    parser.add_argument("--power", type=int, default=200, help="rider power, W")
    parser.add_argument("--target", type=int, help="ERG target power, W")
    parser.add_argument("--mass", type=float, default=78.0, help="rider + bike, kg")
    parser.add_argument("--rate", type=float, default=4.0, help="samples per second")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.mode == Mode.ERG.value and args.target is None:
        parser.error("--mode erg requires --target")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    device = SimulatedDevice(
        mode=Mode(args.mode),
        rider_power=args.power,
        rate_hz=args.rate,
        seed=args.seed,
    )
    try:
        state = asyncio.run(
            run(
                device,
                params=RiderParams(mass_kg=args.mass),
                course_grade=args.grade,
                duration_s=args.duration,
                target_power=args.target,
            )
        )
    except KeyboardInterrupt:
        return 130
    avg_kmh = state.distance / state.elapsed * 3.6 if state.elapsed else 0.0
    print(
        f"Done: {state.distance / 1000.0:.3f} km in {state.elapsed:.0f} s "
        f"(avg {avg_kmh:.1f} km/h)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
