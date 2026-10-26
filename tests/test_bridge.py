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

"""Bridge tests: the HUD page and the SSE ride stream over a real local socket."""

import asyncio
import json
from typing import Any

from rullo.bridge import RideInfo, serve
from rullo.devices.base import Mode
from rullo.devices.simulated import SimulatedDevice


async def _fast_sleep(_: float) -> None:
    await asyncio.sleep(0)


_open: list[asyncio.StreamWriter] = []


async def _get(port: int, path: str) -> tuple[asyncio.StreamReader, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    _open.append(writer)  # a dropped writer closes the socket when collected
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    status = (await reader.readline()).decode()
    while (await reader.readline()).strip():
        pass
    return reader, status


async def _events(
    reader: asyncio.StreamReader, until: str
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name = ""
    while True:
        raw = await asyncio.wait_for(reader.readline(), 5)
        assert raw, "stream closed early"
        line = raw.decode().strip()
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append((name, json.loads(line.removeprefix("data: "))))
            if name == until:
                return events


async def _start(info: RideInfo) -> tuple[asyncio.Task[object], int]:
    ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    device = SimulatedDevice(mode=info.mode, rate_hz=4.0, sleep=_fast_sleep)
    task = asyncio.ensure_future(
        serve(device, info, port=0, on_ready=ready.set_result, linger=True)
    )
    url = await ready
    return task, int(url.rsplit(":", 1)[1].rstrip("/"))


async def test_serves_the_hud_page() -> None:
    task, port = await _start(RideInfo(duration_s=1.0))
    reader, status = await _get(port, "/")
    assert " 200 " in status
    assert "<title>Rullo</title>" in (await reader.read()).decode()
    _, missing = await _get(port, "/nope")
    assert " 404 " in missing
    task.cancel()


async def test_streams_config_samples_and_done() -> None:
    info = RideInfo(mode=Mode.ERG, target_power=300, duration_s=5.0, ftp=280)
    task, port = await _start(info)
    reader, status = await _get(port, "/events")
    assert " 200 " in status
    events = await _events(reader, until="done")
    assert events[0][0] == "config"
    assert events[0][1]["mode"] == "erg" and events[0][1]["ftp"] == 280
    samples = [data for name, data in events if name == "sample"]
    assert samples[-1]["t"] == 5.0
    assert samples[-1]["distance"] > 0.0
    assert {"power", "cadence", "heart_rate", "speed"} <= samples[-1].keys()
    assert events[-1][1]["distance"] == samples[-1]["distance"]
    task.cancel()


async def test_late_client_gets_the_whole_ride_replayed() -> None:
    task, port = await _start(RideInfo(duration_s=2.0))
    first, _ = await _get(port, "/events")
    early = await _events(first, until="done")
    late, _ = await _get(port, "/events")
    assert await _events(late, until="done") == early
    task.cancel()
