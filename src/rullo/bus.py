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

"""Async pub/sub bus fanning telemetry out to all subscribers."""

import asyncio
from collections.abc import AsyncIterator

from rullo.telemetry import Telemetry


class TelemetryBus:
    """Each subscriber gets its own unbounded queue; publish never blocks."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[Telemetry]] = set()

    def subscribe(self) -> AsyncIterator[Telemetry]:
        # Register eagerly so samples published right after this call are kept.
        queue: asyncio.Queue[Telemetry] = asyncio.Queue()
        self._queues.add(queue)
        return self._iterate(queue)

    async def _iterate(
        self, queue: asyncio.Queue[Telemetry]
    ) -> AsyncIterator[Telemetry]:
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.discard(queue)

    def publish(self, t: Telemetry) -> None:
        for queue in self._queues:
            queue.put_nowait(t)
