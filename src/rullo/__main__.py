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

"""Command line: ``rullo`` opens the app; ``rullo ride`` rides in the terminal."""

import argparse
import asyncio
import logging
import sys
import webbrowser
from collections.abc import Sequence

from rullo.app import App, serve_app
from rullo.config import Settings, config_dir, data_dir
from rullo.devices.base import Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.library import Library
from rullo.loop import SessionState, run
from rullo.physics import RiderParams
from rullo.resistance import ResistanceController, ResistanceSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rullo", description="Offline indoor-cycling training for Linux."
    )
    sub = parser.add_subparsers(dest="command")
    app = sub.add_parser("app", help="open the app in a browser (the default)")
    app.add_argument("--port", type=int, default=8765)
    app.add_argument("--no-browser", action="store_true", help="just print the URL")

    ride = sub.add_parser("ride", help="ride the simulated trainer in the terminal")
    ride.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.SIM.value)
    ride.add_argument("--duration", type=float, default=60.0, help="seconds")
    ride.add_argument("--grade", type=float, default=0.0, help="constant grade, %%")
    ride.add_argument("--route", help="route id, e.g. alpine-loop (overrides --grade)")
    ride.add_argument("--workout", help="workout id; implies ERG mode")
    ride.add_argument("--power", type=int, default=200, help="rider power, W")
    ride.add_argument("--target", type=int, help="ERG target power, W")
    ride.add_argument("--mass", type=float, default=78.0, help="rider + bike, kg")
    ride.add_argument("--ftp", type=int, default=250, help="W, for workout targets")
    ride.add_argument("--rate", type=float, default=4.0, help="samples per second")
    ride.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ride":
            return _ride(parser, args)
        return _app(getattr(args, "port", 8765), getattr(args, "no_browser", False))
    except KeyboardInterrupt:
        return 130


def _app(port: int, no_browser: bool) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    settings_path = config_dir() / "settings.json"
    app = App(Library(data_dir()), Settings.load(settings_path), settings_path)

    def ready(url: str) -> None:
        print(f"Rullo is running at {url} (Ctrl-C to quit)", flush=True)
        if not no_browser:
            webbrowser.open(url)

    try:
        asyncio.run(serve_app(app, port=port, on_ready=ready))
    except OSError as exc:
        print(f"rullo: cannot listen on port {port}: {exc}", file=sys.stderr)
        return 1
    return 0


def _ride(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    library = Library(data_dir())
    mode = Mode(args.mode)
    course = workout = None
    if args.route:
        course = library.routes().get(args.route)
        if course is None:
            parser.error(
                f"unknown route {args.route!r}; try: {', '.join(library.routes())}"
            )
    if args.workout:
        workout = library.workouts().get(args.workout)
        if workout is None:
            parser.error(
                f"unknown workout {args.workout!r}; "
                f"try: {', '.join(library.workouts())}"
            )
        mode = Mode.ERG
    elif mode is Mode.ERG and args.target is None:
        parser.error("--mode erg requires --target or --workout")
    device = SimulatedDevice(
        mode=mode, rider_power=args.power, rate_hz=args.rate, seed=args.seed
    )
    controller = ResistanceController(
        device, mode, args.ftp, workout, args.target, ResistanceSettings()
    )
    state = asyncio.run(
        run(
            device,
            params=RiderParams(mass_kg=args.mass),
            course_grade=args.grade,
            duration_s=workout.duration if workout else args.duration,
            course=course,
            controller=controller,
        )
    )
    _print_done(state)
    return 0


def _print_done(state: SessionState) -> None:
    avg_kmh = state.distance / state.elapsed * 3.6 if state.elapsed else 0.0
    print(
        f"Done: {state.distance / 1000.0:.3f} km in {state.elapsed:.0f} s "
        f"(avg {avg_kmh:.1f} km/h)",
        flush=True,
    )


if __name__ == "__main__":
    sys.exit(main())
