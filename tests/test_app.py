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

"""App tests: the HTTP API, a full ride over SSE, saving and uploading."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fitdecode
import pytest

from conftest import FakeApi
from rullo.app import App, RideRequest, serve_app
from rullo.config import Settings
from rullo.devices.base import Device
from rullo.devices.simulated import SimulatedDevice
from rullo.library import Library

SHORT_ZWO = b"""<workout_file><name>Quick Test</name><workout>
<SteadyState Duration="10" Power="0.6"/>
<SteadyState Duration="10" Power="1.2"/>
<SteadyState Duration="10" Power="0.6"/>
</workout></workout_file>"""


async def _fast(_: float) -> None:
    await asyncio.sleep(0)


def _sim(req: RideRequest, s: Settings) -> Device:
    return SimulatedDevice(mode=req.mode, rider_power=s.sim_power, sleep=_fast)


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        data = raw if raw is not None else (json.dumps(body).encode() if body else b"")
        head = {
            "Host": f"127.0.0.1:{self.port}",
            "X-Rullo": "1",
            "Content-Length": str(len(data)),
        } | (headers or {})
        lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in head.items()]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + data)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        header_blob, _, payload = response.partition(b"\r\n\r\n")
        status_line, *header_lines = header_blob.decode().split("\r\n")
        parsed = {
            k.lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)
        }
        return int(status_line.split()[1]), parsed, payload

    async def json(self, method: str, path: str, body: Any = None) -> Any:
        status, _, payload = await self.request(method, path, body)
        assert status == 200, payload
        return json.loads(payload)

    async def events(self) -> AsyncIterator[tuple[str, Any]]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(
            f"GET /events HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode()
        )
        await writer.drain()
        while (await reader.readline()).strip():
            pass
        name = ""
        try:
            while line := await asyncio.wait_for(reader.readline(), 10):
                text = line.decode().strip()
                if text.startswith("event: "):
                    name = text.removeprefix("event: ")
                elif text.startswith("data: "):
                    yield name, json.loads(text.removeprefix("data: "))
        finally:
            writer.close()


@pytest.fixture
async def running(
    tmp_path: Path, fake_api: FakeApi
) -> AsyncIterator[tuple[App, Client]]:
    settings = Settings(
        ftp=200,
        intervals_api_key="KEY",
        strava_client_id="42",
        strava_client_secret="SECRET",
    )
    app = App(
        Library(tmp_path / "data"),
        settings,
        tmp_path / "settings.json",
        device_factory=_sim,
        intervals_api=fake_api.url,
        strava_auth=fake_api.url,
        strava_api=fake_api.url,
    )
    ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    task = asyncio.ensure_future(serve_app(app, port=0, on_ready=ready.set_result))
    port = int((await ready).rsplit(":", 1)[1].rstrip("/"))
    yield app, Client(port)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _until(
    events: AsyncIterator[tuple[str, Any]], name: str
) -> list[tuple[str, Any]]:
    seen = []
    async for event in events:
        seen.append(event)
        if event[0] == name:
            return seen
    raise AssertionError(f"stream ended before {name}")


async def test_page_library_and_settings(running: tuple[App, Client]) -> None:
    _, client = running
    status, headers, page = await client.request("GET", "/")
    assert status == 200 and b"<title>Rullo</title>" in page
    lib = await client.json("GET", "/api/library")
    assert {r["id"] for r in lib["routes"]} >= {"alpine-loop", "col-climb"}
    vo2 = next(w for w in lib["workouts"] if w["id"] == "vo2-intervals")
    assert vo2["tss"] > 0 and len(vo2["steps"]) == 14
    settings = await client.json("POST", "/api/settings", {"ftp": "280", "mass_kg": 72})
    assert settings["ftp"] == 280 and settings["mass_kg"] == 72.0
    assert "intervals_api_key" not in settings and settings["intervals_api_key_set"]
    status, _, _ = await client.request(
        "POST", "/api/settings", {"strava_access_token": "x"}
    )
    assert status == 400


async def test_requests_from_other_origins_are_refused(
    running: tuple[App, Client],
) -> None:
    _, client = running
    status, _, _ = await client.request("POST", "/api/ride/start", {}, {"X-Rullo": "0"})
    assert status == 403
    status, _, _ = await client.request(
        "GET", "/api/settings", headers={"Host": "evil.test"}
    )
    assert status == 403


async def test_full_workout_ride_is_recorded_and_uploaded(
    running: tuple[App, Client], fake_api: FakeApi
) -> None:
    app, client = running
    status, _, body = await client.request(
        "POST", "/api/workouts?filename=q.zwo", raw=SHORT_ZWO
    )
    assert status == 200 and json.loads(body)["id"] == "quick-test"
    events = client.events()
    assert (await anext(events))[0] == "idle"
    ride = await client.json(
        "POST", "/api/ride/start", {"workout": "quick-test", "route": "alpine-loop"}
    )
    seen = await _until(events, "done")
    config = seen[0][1]
    assert seen[0][0] == "config" and config["mode"] == "erg"
    assert config["name"] == "Quick Test on Alpine Loop"
    samples = [d for n, d in seen if n == "sample"]
    assert samples[-1]["t"] == pytest.approx(30.0)
    assert {s["target"] for s in samples} >= {120, 240}
    assert samples[-1]["elevation"] != 0.0
    done = seen[-1][1]
    assert done["id"] == ride["id"]
    assert len(done["summary"]["laps"]) == 3
    assert done["summary"]["normalized_power"] > done["summary"]["avg_power"] - 5

    history = await client.json("GET", "/api/rides")
    assert [r["id"] for r in history] == [ride["id"]] and "trace" not in history[0]
    status, headers, fit = await client.request("GET", f"/api/rides/{ride['id']}/fit")
    assert status == 200 and "attachment" in headers["content-disposition"]
    with fitdecode.FitReader(fit, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        names = [f.name for f in reader if f.frame_type == fitdecode.FIT_FRAME_DATA]
    assert names.count("lap") == 3 and "session" in names

    fake_api.reply("POST", "/athlete/0/activities", 200, {"id": "i9"})
    result = await client.json("POST", f"/api/rides/{ride['id']}/upload/intervals")
    assert result["url"].endswith("/i9")
    saved = await client.json("GET", f"/api/rides/{ride['id']}")
    assert saved["uploads"]["intervals"]["activity_id"] == "i9"
    assert not app.riding


async def test_stopping_a_ride_still_saves_it(running: tuple[App, Client]) -> None:
    app, client = running
    events = client.events()
    await client.json("POST", "/api/ride/start", {"mode": "sim", "route": "col-climb"})
    async for name, data in events:
        if name == "sample" and data["t"] >= 5.0:
            break
    status, _, _ = await client.request("POST", "/api/ride/start", {"mode": "sim"})
    assert status == 409  # one ride at a time
    assert (await client.json("POST", "/api/ride/stop")) == {"riding": False}
    rides = await client.json("GET", "/api/rides")
    assert len(rides) == 1 and rides[0]["summary"]["distance"] > 0
    assert not app.riding


async def test_upload_errors_are_reported(
    running: tuple[App, Client], fake_api: FakeApi
) -> None:
    _, client = running
    events = client.events()
    await client.json(
        "POST", "/api/ride/start", {"mode": "erg", "target_power": 200, "duration_s": 3}
    )
    ride = (await _until(events, "done"))[-1][1]
    status, _, body = await client.request(
        "POST", f"/api/rides/{ride['id']}/upload/strava"
    )
    assert status == 502 and b"not connected" in body
    status, _, _ = await client.request("GET", "/api/rides/20990101T000000Z")
    assert status == 404


async def test_strava_oauth_round_trip(
    running: tuple[App, Client], fake_api: FakeApi
) -> None:
    app, client = running
    status, headers, _ = await client.request("GET", "/api/strava/connect")
    assert status == 302
    location = headers["location"]
    assert (
        location.startswith(fake_api.url + "/oauth/authorize?")
        and "client_id=42" in location
    )
    status, headers, _ = await client.request(
        "GET", "/api/strava/callback?code=C&state=wrong"
    )
    assert headers["location"] == "/?strava=bad-state#settings"
    _, headers, _ = await client.request(
        "GET", "/api/strava/connect"
    )  # a wrong state burns it
    state = headers["location"].split("state=")[1].split("&")[0]
    fake_api.reply(
        "POST",
        "/oauth/token",
        200,
        {"access_token": "A", "refresh_token": "R", "expires_at": 2_000_000_000},
    )
    status, headers, _ = await client.request(
        "GET", f"/api/strava/callback?code=C&state={state}"
    )
    assert headers["location"] == "/?strava=connected#settings"
    assert app.settings.strava_refresh_token == "R"
    assert (await client.json("GET", "/api/settings"))["strava_connected"]


async def test_bad_ride_requests(running: tuple[App, Client]) -> None:
    _, client = running
    for body in ({"mode": "erg"}, {"route": "nowhere"}, {"mode": "warp"}):
        status, _, _ = await client.request("POST", "/api/ride/start", body)
        assert status == 400, body
    status, _, _ = await client.request(
        "POST", "/api/routes?filename=x.gpx", raw=b"<gpx/>"
    )
    assert status == 400


async def test_devices_report_scan_errors(tmp_path: Path) -> None:
    async def broken_scan(radio: str) -> list[Any]:
        raise OSError(
            "No Bluetooth adapters found." if radio == "bluetooth" else "No stick"
        )

    app = App(Library(tmp_path), Settings(), tmp_path / "s.json", scanner=broken_scan)
    result = await app._devices(scan=True)
    assert result["devices"][0]["id"] == "sim"
    assert result["error"] == "Bluetooth scan failed: No Bluetooth adapters found."
    result = await app._devices(scan=True, radio="ant")
    assert result["error"] == "ANT+ scan failed: No stick"


async def test_radios_cannot_be_mixed(running: tuple[App, Client]) -> None:
    _, client = running
    cases = [
        {"trainer": "C2:AF:AB:5E:07:46", "heart_rate": "ant:120:1"},
        {"trainer": "ant:17:1", "cadence": "AA:BB:CC:DD:EE:FF"},
        {"trainer": "sim", "heart_rate": "ant:120:1"},
    ]
    for body in cases:
        status, _, payload = await client.request("POST", "/api/ride/start", body)
        assert status == 400, body
    status, _, _ = await client.request("GET", "/api/devices?radio=wifi")
    assert status == 400
