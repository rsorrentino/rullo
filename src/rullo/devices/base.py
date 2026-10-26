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

"""The Device protocol every real or fake trainer implements."""

from collections.abc import AsyncIterator
from typing import Protocol

from rullo.telemetry import Telemetry


class Device(Protocol):
    """A source of telemetry that can also accept trainer commands."""

    async def connect(self) -> None: ...

    def telemetry(self) -> AsyncIterator[Telemetry]: ...

    async def set_grade(self, percent: float) -> None: ...

    async def set_target_power(self, watts: float) -> None: ...

    async def disconnect(self) -> None: ...
