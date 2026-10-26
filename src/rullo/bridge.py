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

"""Bridge: serves the web HUD and streams the ride to it as JSON over SSE.

The renderer is a separate client that only sees this local HTTP socket, so the
core keeps zero rendering imports. Every event is kept and replayed to clients
that connect late or reload, so the ride summary always has the full trace.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from importlib.resources import files

from rullo.bus import TelemetryBus
from rullo.devices.base import Device, Mode
from rullo.loop import SessionState, run_loop
from rullo.physics import RiderParams
from rullo.telemetry import Telemetry


@dataclass(frozen=True)
class RideInfo:
    """Static ride settings the HUD needs alongside the telemetry."""

    mode: Mode = Mode.SIM
    grade: float = 0.0  # %
    target_power: int | None = None  # W, ERG mode
    duration_s: float = 60.0
    ftp: int = 250  # W, for power zones
    mass_kg: float = 78.0


def _frame(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class Bridge:
    """Fans bus samples out to any number of browser clients."""

    def __init__(self, info: RideInfo, state: SessionState) -> None:
        self._state = state
        config = asdict(info) | {"mode": info.mode.value}
        self._history: list[bytes] = [_frame("config", config)]
        self._clients: set[asyncio.Queue[bytes]] = set()

    def _emit(self, frame: bytes) -> None:
        self._history.append(frame)
        for queue in self._clients:
            queue.put_nowait(frame)

    async def pump(self, samples: AsyncIterator[Telemetry]) -> None:
        async for sample in samples:
            self._emit(
                _frame("sample", asdict(sample) | {"distance": self._state.distance})
            )

    def finish(self) -> None:
        self._emit(_frame("done", asdict(self._state)))

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = (await reader.readline()).decode(errors="replace").split()
            while (await reader.readline()).strip():
                pass  # headers are not needed
            path = request[1] if len(request) > 1 else ""
            if path == "/":
                await _send_page(writer)
            elif path == "/events":
                await self._stream(writer)
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        for frame in self._history:
            queue.put_nowait(frame)
        self._clients.add(queue)
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
            )
            while True:
                writer.write(await queue.get())
                await writer.drain()
        finally:
            self._clients.discard(queue)


async def _send_page(writer: asyncio.StreamWriter) -> None:
    body = (files("rullo") / "web" / "index.html").read_bytes()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def serve(
    device: Device,
    info: RideInfo,
    host: str = "127.0.0.1",
    port: int = 8765,
    on_ready: Callable[[str], None] | None = None,
    on_done: Callable[[SessionState], None] | None = None,
    linger: bool = True,
) -> SessionState:
    """Ride a session while serving the web HUD; keep serving after if ``linger``."""
    bus = TelemetryBus()
    state = SessionState()
    bridge = Bridge(info, state)
    pump = asyncio.ensure_future(bridge.pump(bus.subscribe()))
    server = await asyncio.start_server(bridge.handle, host, port)
    try:
        bound_port = server.sockets[0].getsockname()[1]
        if on_ready is not None:
            on_ready(f"http://{host}:{bound_port}/")
        await run_loop(
            device,
            bus,
            params=RiderParams(mass_kg=info.mass_kg),
            grade=info.grade,
            target_power=info.target_power,
            duration_s=info.duration_s,
            state=state,
        )
        await asyncio.sleep(0)  # let the pump forward the last sample
        bridge.finish()
        if on_done is not None:
            on_done(state)
        if linger:
            await asyncio.Event().wait()  # until Ctrl-C
        return state
    finally:
        pump.cancel()
        server.close()
        await asyncio.gather(pump, return_exceptions=True)
