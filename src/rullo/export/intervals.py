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

"""intervals.icu: activity upload with an API key (HTTP basic auth)."""

import base64
from pathlib import Path
from typing import Any

from rullo.config import Settings
from rullo.export.http import UploadError, multipart, request

API_BASE = "https://intervals.icu/api/v1"


def upload(
    settings: Settings, fit: Path, name: str, external_id: str, api: str = API_BASE
) -> dict[str, Any]:
    if not settings.intervals_api_key:
        raise UploadError("add your intervals.icu API key in Settings")
    auth = base64.b64encode(f"API_KEY:{settings.intervals_api_key}".encode()).decode()
    body, ctype = multipart(
        {"name": name, "external_id": external_id}, "file", fit.name, fit.read_bytes()
    )
    athlete = settings.intervals_athlete_id or "0"
    reply = request(
        "POST",
        f"{api}/athlete/{athlete}/activities",
        {"Authorization": f"Basic {auth}", "Content-Type": ctype},
        body,
    )
    activity = reply.get("id") if isinstance(reply, dict) else None
    return {
        "activity_id": activity,
        "url": f"https://intervals.icu/activities/{activity}" if activity else None,
    }
