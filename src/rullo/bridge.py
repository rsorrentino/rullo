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

"""Bridge: fans ride events out to browser clients as Server-Sent Events.

The renderer is a separate client that only sees the local socket, so the core
keeps zero rendering imports. Every event of the current ride is kept and
replayed to clients that connect late or reload.
"""

import asyncio
import json
from collections.abc import AsyncGenerator


def frame(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class EventHub:
    """Holds the current ride's events and streams them to every client."""

    def __init__(self) -> None:
        self._history: list[bytes] = [frame("idle", {})]
        self._clients: set[asyncio.Queue[bytes]] = set()

    def reset(self, event: str, data: object) -> None:
        """Start a new history (a new ride, or back to idle)."""
        self._history = []
        self.emit(event, data)

    def emit(self, event: str, data: object) -> None:
        encoded = frame(event, data)
        self._history.append(encoded)
        for queue in self._clients:
            queue.put_nowait(encoded)

    async def stream(self) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        for encoded in self._history:
            queue.put_nowait(encoded)
        self._clients.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._clients.discard(queue)
