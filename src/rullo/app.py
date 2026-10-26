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

"""The Rullo app: local web UI and JSON API around one ride at a time."""

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from rullo.bridge import EventHub
from rullo.bus import TelemetryBus
from rullo.config import Settings
from rullo.devices.base import Device, Mode
from rullo.devices.simulated import SimulatedDevice
from rullo.export import intervals, strava
from rullo.export.http import UploadError
from rullo.library import Library, route_info, workout_info
from rullo.loop import SessionState, run_loop
from rullo.physics import RiderParams
from rullo.recorder import Point, ride_filename, write_fit
from rullo.resistance import ResistanceController, ResistanceSettings
from rullo.session import SessionMetrics
from rullo.webserver import (
    HttpError,
    Request,
    Response,
    Stream,
    json_response,
    redirect,
    serve_handler,
)

logger = logging.getLogger(__name__)

# Bundled fonts (SIL Open Font License; the license files sit next to them).
_FONTS = {
    "/fonts/inter.woff2": "inter.woff2",
    "/fonts/space-grotesk.woff2": "space-grotesk.woff2",
}
_RIDE_URL = re.compile(r"^/api/rides/([0-9TZ]+)(/fit|/upload/(strava|intervals))?$")


@dataclass(frozen=True)
class RideRequest:
    trainer: str = "sim"  # "sim" or a Bluetooth address
    heart_rate: str | None = None  # Bluetooth address of an HR strap
    cadence: str | None = None  # Bluetooth address of a cadence sensor
    route: str | None = None
    workout: str | None = None
    mode: Mode = Mode.SIM
    target_power: int | None = None
    duration_s: float | None = None

    @classmethod
    def parse(cls, raw: Any, library: Library) -> "RideRequest":
        if not isinstance(raw, dict):
            raise HttpError(400, "expected a JSON object")
        try:
            mode = Mode(raw.get("mode") or "sim")
        except ValueError as exc:
            raise HttpError(400, f"unknown mode {raw.get('mode')!r}") from exc
        route, workout = raw.get("route") or None, raw.get("workout") or None
        if route is not None and route not in library.routes():
            raise HttpError(400, f"unknown route {route!r}")
        if workout is not None and workout not in library.workouts():
            raise HttpError(400, f"unknown workout {workout!r}")
        trainer = str(raw.get("trainer") or "sim")
        sensors = [s for s in (raw.get("heart_rate"), raw.get("cadence")) if s]
        if trainer != "sim" and sensors:
            trainer_ant = trainer.startswith("ant:")
            if any(str(s).startswith("ant:") != trainer_ant for s in sensors):
                raise HttpError(
                    400,
                    "pair the trainer and sensors on the same radio "
                    "(all Bluetooth or all ANT+)",
                )
        if trainer == "sim" and sensors:
            raise HttpError(
                400, "sensors cannot be combined with the simulated trainer"
            )
        target = raw.get("target_power")
        duration = raw.get("duration_s")
        if workout is not None:
            mode = Mode.ERG
        elif mode is Mode.ERG and not target:
            raise HttpError(400, "ERG mode needs a workout or a target power")
        return cls(
            trainer=str(raw.get("trainer") or "sim"),
            heart_rate=raw.get("heart_rate") or None,
            cadence=raw.get("cadence") or None,
            route=route,
            workout=workout,
            mode=mode,
            target_power=int(target) if target else None,
            duration_s=float(duration) if duration else None,
        )


DeviceFactory = Callable[[RideRequest, Settings], Device]
Scanner = Callable[[str], Awaitable[list[Any]]]  # radio: "bluetooth" or "ant"


def default_device(req: RideRequest, settings: Settings) -> Device:
    if req.trainer == "sim":
        return SimulatedDevice(
            mode=req.mode, rider_power=settings.sim_power, seed=secrets.randbits(32)
        )
    from rullo.devices.ant import AntTrainer, is_ant

    if is_ant(req.trainer):
        return AntTrainer(
            req.trainer,
            hr=req.heart_rate,
            cadence=req.cadence,
            mass_kg=settings.mass_kg,
        )
    from rullo.devices.ble import BleTrainer

    return BleTrainer(
        req.trainer,
        hr_address=req.heart_rate,
        cadence_address=req.cadence,
        mass_kg=settings.mass_kg,
    )


async def default_scan(radio: str = "bluetooth") -> list[Any]:
    if radio == "ant":
        from rullo.devices.ant import scan as ant_scan

        return list(await ant_scan())
    from rullo.devices.ble import scan

    return list(await scan())


class App:
    def __init__(
        self,
        library: Library,
        settings: Settings,
        settings_path: Path,
        device_factory: DeviceFactory = default_device,
        scanner: Scanner = default_scan,
        strava_auth: str = strava.AUTH_BASE,
        strava_api: str = strava.API_BASE,
        intervals_api: str = intervals.API_BASE,
    ) -> None:
        self.library = library
        self.settings = settings
        self._settings_path = settings_path
        self._device_factory = device_factory
        self._scanner = scanner
        self._strava_auth, self._strava_api = strava_auth, strava_api
        self._intervals_api = intervals_api
        self.hub = EventHub()
        self._ride: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._oauth_state = ""

    @property
    def riding(self) -> bool:
        return self._ride is not None and not self._ride.done()

    # --- rides ---------------------------------------------------------------

    def start_ride(self, req: RideRequest) -> str:
        if self.riding:
            raise HttpError(409, "a ride is already in progress")
        start = datetime.now(UTC)
        ride_id = ride_filename(start).removesuffix(".fit")
        self._stop = asyncio.Event()
        self._ride = asyncio.ensure_future(self._run_ride(req, ride_id, start))
        return ride_id

    async def stop_ride(self) -> None:
        if self._ride is not None and not self._ride.done():
            self._stop.set()
            await asyncio.shield(self._ride)

    async def _run_ride(self, req: RideRequest, ride_id: str, start: datetime) -> None:
        s = self.settings
        course = self.library.routes()[req.route] if req.route else None
        workout = self.library.workouts()[req.workout] if req.workout else None
        names = [n for n in (workout and workout.name, course and course.name) if n]
        name = " on ".join(names) or f"{req.mode.value.upper()} ride"
        duration = workout.duration if workout else req.duration_s
        config: dict[str, Any] = {
            "id": ride_id,
            "name": name,
            "mode": req.mode.value,
            "trainer": "Simulated trainer" if req.trainer == "sim" else req.trainer,
            "ftp": s.ftp,
            "mass_kg": s.mass_kg,
            "duration_s": duration,
            "target_power": req.target_power,
            "route": route_info(req.route, course) if course and req.route else None,
            "workout": (
                workout_info(req.workout, workout) if workout and req.workout else None
            ),
        }
        self.hub.reset("config", config)
        metrics = SessionMetrics(s.ftp)
        points: list[Point] = []
        error: str | None = None
        try:
            device = self._device_factory(req, s)
            controller = ResistanceController(
                device,
                req.mode,
                s.ftp,
                workout,
                req.target_power,
                ResistanceSettings(
                    difficulty=s.difficulty, cadence_floor=s.cadence_floor
                ),
            )
            bus, state = TelemetryBus(), SessionState()
            pump = asyncio.ensure_future(
                self._pump(bus, state, controller, metrics, points, workout, course)
            )
            try:
                await run_loop(
                    device,
                    bus,
                    RiderParams(mass_kg=s.mass_kg),
                    course=course,
                    controller=controller,
                    stop=self._stop,
                    duration_s=duration,
                    state=state,
                )
                await asyncio.sleep(0)  # let the pump record the last sample
            finally:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
        except Exception as exc:  # the ride so far is still saved below
            logger.exception("ride failed")
            error = str(exc) or type(exc).__name__
        if not points:
            self.hub.reset("idle", {"error": error or "the ride ended before any data"})
            return
        ride = self._save(ride_id, start, config, points, metrics, error)
        self.hub.emit("done", ride)

    async def _pump(
        self,
        bus: TelemetryBus,
        state: SessionState,
        controller: ResistanceController,
        metrics: SessionMetrics,
        points: list[Point],
        workout: Any,
        course: Any,
    ) -> None:
        lap_key: int | None = None
        async for sample in bus.subscribe():
            if workout is not None:
                key = workout.step_index(sample.t)
            elif course is not None:
                key = int(state.distance // course.length)
            else:
                key = 0
            if lap_key is not None and key != lap_key:
                metrics.new_lap(sample.t, state.distance)
            lap_key = key
            metrics.add(
                sample.t,
                sample.power,
                sample.heart_rate,
                sample.cadence,
                state.distance,
                state.elevation,
            )
            points.append(
                Point(
                    sample.t,
                    sample.power,
                    sample.heart_rate,
                    sample.cadence,
                    sample.speed or 0.0,
                    state.distance,
                    state.elevation,
                )
            )
            target = (
                controller.planned_target(sample.t)
                if controller.mode is Mode.ERG
                else None
            )
            self.hub.emit(
                "sample",
                {
                    "t": sample.t,
                    "power": sample.power,
                    "cadence": sample.cadence,
                    "heart_rate": sample.heart_rate,
                    "speed": sample.speed,
                    "distance": state.distance,
                    "grade": state.grade,
                    "elevation": state.elevation,
                    "target": target,
                    "released": controller.released,
                    "step": key,
                    "np": metrics.normalized_power,
                    "tss": round(metrics.tss, 1),
                },
            )

    def _save(
        self,
        ride_id: str,
        start: datetime,
        config: dict[str, Any],
        points: list[Point],
        metrics: SessionMetrics,
        error: str | None,
    ) -> dict[str, Any]:
        write_fit(self.library.rides_dir / f"{ride_id}.fit", start, points, metrics)
        trace, next_t = [], 0.0
        for p in points:  # about one point per second is plenty for charts
            if p.t >= next_t:
                trace.append(
                    [
                        round(p.t, 2),
                        p.power,
                        p.heart_rate,
                        p.cadence,
                        round(p.speed, 2),
                        round(p.elevation, 1),
                    ]
                )
                next_t = p.t + 1.0
        ride = {
            "id": ride_id,
            "started_at": start.isoformat(),
            "name": config["name"],
            "mode": config["mode"],
            "trainer": config["trainer"],
            "route": config["route"]["name"] if config["route"] else None,
            "workout": config["workout"]["name"] if config["workout"] else None,
            "summary": metrics.summary(),
            "trace": trace,
            "uploads": {},
            "error": error,
        }
        self.library.save_ride(ride_id, ride)
        return ride

    # --- uploads -------------------------------------------------------------

    async def upload(self, ride_id: str, service: str) -> dict[str, Any]:
        ride = self.library.ride(ride_id)
        fit = self.library.ride_path(ride_id, ".fit")
        name = f"Rullo · {ride['name']}"
        try:
            if service == "strava":
                result = await asyncio.to_thread(
                    strava.upload,
                    self.settings,
                    fit,
                    name,
                    f"rullo-{ride_id}",
                    self._strava_api,
                    self._strava_auth,
                )
            else:
                result = await asyncio.to_thread(
                    intervals.upload,
                    self.settings,
                    fit,
                    name,
                    f"rullo-{ride_id}",
                    self._intervals_api,
                )
        except UploadError as exc:
            raise HttpError(502, str(exc)) from exc
        finally:
            self.settings.save(self._settings_path)  # keep refreshed tokens
        ride["uploads"][service] = result
        self.library.save_ride(ride_id, ride)
        return result

    # --- HTTP ----------------------------------------------------------------

    async def handle(self, req: Request) -> Response | Stream:
        path, method = req.path, req.method
        if path == "/" and method == "GET":
            page = (files("rullo") / "web" / "index.html").read_bytes()
            return Response(200, page, "text/html; charset=utf-8")
        if path in ("/logo.svg", "/favicon.svg", "/favicon.ico") and method == "GET":
            logo = (files("rullo") / "web" / "logo.svg").read_bytes()
            return Response(200, logo, "image/svg+xml")
        if path in _FONTS and method == "GET":
            font = (files("rullo") / "web" / "fonts" / _FONTS[path]).read_bytes()
            return Response(200, font, "font/woff2", {"Cache-Control": "max-age=86400"})
        if path == "/events" and method == "GET":
            return Stream(self.hub.stream())
        if path == "/api/library" and method == "GET":
            return json_response(
                {
                    "routes": [
                        route_info(k, c) for k, c in self.library.routes().items()
                    ],
                    "workouts": [
                        workout_info(k, w) for k, w in self.library.workouts().items()
                    ],
                }
            )
        if path in ("/api/routes", "/api/workouts") and method == "POST":
            filename = req.query.get("filename", "import")
            try:
                if path == "/api/routes":
                    key = self.library.import_route(filename, req.body)
                else:
                    key = self.library.import_workout(filename, req.body)
            except (ValueError, KeyError, SyntaxError, UnicodeDecodeError) as exc:
                raise HttpError(400, f"could not import {filename}: {exc}") from exc
            return json_response({"id": key})
        if path == "/api/settings":
            if method == "POST":
                body = req.json()
                if not isinstance(body, dict):
                    raise HttpError(400, "expected a JSON object")
                try:
                    self.settings.update(body)
                except (ValueError, TypeError) as exc:
                    raise HttpError(400, str(exc)) from exc
                self.settings.save(self._settings_path)
            return json_response(self.settings.public())
        if path == "/api/devices" and method == "GET":
            radio = req.query.get("radio", "bluetooth")
            if radio not in ("bluetooth", "ant"):
                raise HttpError(400, f"unknown radio {radio!r}")
            return json_response(
                await self._devices(req.query.get("scan") == "1", radio)
            )
        if path == "/api/ride/start" and method == "POST":
            ride_id = self.start_ride(RideRequest.parse(req.json(), self.library))
            return json_response({"id": ride_id})
        if path == "/api/ride/stop" and method == "POST":
            await self.stop_ride()
            return json_response({"riding": self.riding})
        if path == "/api/state" and method == "GET":
            return json_response({"riding": self.riding})
        if path == "/api/rides" and method == "GET":
            return json_response(self.library.rides())
        if match := _RIDE_URL.match(path):
            return await self._ride_endpoint(
                req, match.group(1), match.group(2), match.group(3)
            )
        if path == "/api/strava/connect" and method == "GET":
            return self._strava_connect(req)
        if path == "/api/strava/callback" and method == "GET":
            return await self._strava_callback(req)
        if path == "/api/strava/disconnect" and method == "POST":
            for key in (
                "strava_access_token",
                "strava_refresh_token",
                "strava_athlete",
            ):
                setattr(self.settings, key, "")
            self.settings.strava_expires_at = 0
            self.settings.save(self._settings_path)
            return json_response(self.settings.public())
        raise HttpError(404, f"no route for {method} {path}")

    async def _ride_endpoint(
        self, req: Request, ride_id: str, sub: str | None, service: str | None
    ) -> Response:
        try:
            if sub is None and req.method == "GET":
                return json_response(self.library.ride(ride_id))
            if sub is None and req.method == "DELETE":
                self.library.delete_ride(ride_id)
                return json_response({"deleted": ride_id})
            if sub == "/fit" and req.method == "GET":
                data = self.library.ride_path(ride_id, ".fit").read_bytes()
                return Response(
                    200,
                    data,
                    "application/vnd.ant.fit",
                    {
                        "Content-Disposition": (
                            f'attachment; filename="rullo-{ride_id}.fit"'
                        )
                    },
                )
            if service and req.method == "POST":
                return json_response(await self.upload(ride_id, service))
        except KeyError as exc:
            raise HttpError(404, f"no ride {ride_id}") from exc
        raise HttpError(405, "method not allowed")

    async def _devices(self, scan: bool, radio: str = "bluetooth") -> dict[str, Any]:
        devices: list[dict[str, Any]] = [
            {
                "id": "sim",
                "name": "Simulated trainer",
                "kind": "trainer",
                "detail": "No hardware needed",
                "rssi": None,
            }
        ]
        error = None
        if scan:
            try:
                for found in await self._scanner(radio):
                    devices.append(
                        {
                            "id": found.address,
                            "name": found.name,
                            "kind": found.kind,
                            "detail": f"{found.detail} · {found.address}",
                            "rssi": found.rssi,
                        }
                    )
            except Exception as exc:  # no adapter, BlueZ down, permissions...
                first = exc.args[0] if exc.args else None
                reason = first if isinstance(first, str) else exc
                label = "ANT+" if radio == "ant" else "Bluetooth"
                logger.warning("%s scan failed: %s", label, reason)
                error = f"{label} scan failed: {reason}"
        return {"devices": devices, "error": error}

    def _strava_connect(self, req: Request) -> Response:
        s = self.settings
        if not (s.strava_client_id and s.strava_client_secret):
            raise HttpError(400, "set the Strava client ID and secret first")
        self._oauth_state = secrets.token_urlsafe(16)
        callback = f"http://{req.headers['host']}/api/strava/callback"
        return redirect(
            strava.authorize_url(
                s.strava_client_id, callback, self._oauth_state, self._strava_auth
            )
        )

    async def _strava_callback(self, req: Request) -> Response:
        state, code = req.query.get("state", ""), req.query.get("code", "")
        if not self._oauth_state or not secrets.compare_digest(
            state, self._oauth_state
        ):
            return redirect("/?strava=bad-state#settings")
        self._oauth_state = ""
        if not code:
            return redirect("/?strava=denied#settings")
        try:
            await asyncio.to_thread(
                strava.exchange_code, self.settings, code, self._strava_auth
            )
        except UploadError as exc:
            logger.warning("Strava token exchange failed: %s", exc)
            return redirect("/?strava=error#settings")
        self.settings.save(self._settings_path)
        return redirect("/?strava=connected#settings")


async def serve_app(
    app: App,
    host: str = "127.0.0.1",
    port: int = 8765,
    on_ready: Callable[[str], None] | None = None,
) -> None:
    """Serve until cancelled; an in-progress ride is stopped and saved first."""
    server = await asyncio.start_server(serve_handler(app.handle), host, port)
    try:
        bound = server.sockets[0].getsockname()[1]
        if on_ready is not None:
            on_ready(f"http://{host}:{bound}/")
        await asyncio.Event().wait()
    finally:
        server.close()
        await app.stop_ride()
