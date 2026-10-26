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

"""Strava: OAuth2 authorisation code flow and activity upload (API v3)."""

import time
import urllib.parse
from pathlib import Path
from typing import Any

from rullo.config import Settings
from rullo.export.http import UploadError, multipart, request

AUTH_BASE = "https://www.strava.com"
API_BASE = "https://www.strava.com/api/v3"
SCOPE = "activity:write,read"


def authorize_url(
    client_id: str, redirect_uri: str, state: str, base: str = AUTH_BASE
) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": SCOPE,
            "state": state,
        }
    )
    return f"{base}/oauth/authorize?{query}"


def _store(settings: Settings, token: dict[str, Any]) -> None:
    settings.strava_access_token = str(token["access_token"])
    settings.strava_refresh_token = str(token["refresh_token"])
    settings.strava_expires_at = int(token["expires_at"])
    athlete = token.get("athlete") or {}
    name = " ".join(filter(None, [athlete.get("firstname"), athlete.get("lastname")]))
    if name:
        settings.strava_athlete = name


def exchange_code(settings: Settings, code: str, base: str = AUTH_BASE) -> None:
    """Swap the OAuth code from the redirect for tokens, stored in ``settings``."""
    token = request(
        "POST",
        f"{base}/oauth/token",
        form={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    _store(settings, token)


def access_token(settings: Settings, base: str = AUTH_BASE) -> str:
    """A valid access token, refreshed when it is about to expire."""
    if not settings.strava_refresh_token:
        raise UploadError("Strava is not connected; connect it in Settings")
    if settings.strava_expires_at - 60 <= time.time():
        token = request(
            "POST",
            f"{base}/oauth/token",
            form={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "refresh_token": settings.strava_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        _store(settings, token)
    return settings.strava_access_token


def upload(
    settings: Settings,
    fit: Path,
    name: str,
    external_id: str,
    api: str = API_BASE,
    auth: str = AUTH_BASE,
    polls: int = 10,
    poll_s: float = 1.5,
) -> dict[str, Any]:
    """Upload a .fit file and wait briefly for Strava to process it."""
    headers = {"Authorization": f"Bearer {access_token(settings, auth)}"}
    body, ctype = multipart(
        {"data_type": "fit", "name": name, "trainer": "1", "external_id": external_id},
        "file",
        fit.name,
        fit.read_bytes(),
    )
    status: dict[str, Any] = request(
        "POST", f"{api}/uploads", headers | {"Content-Type": ctype}, body
    )
    for _ in range(polls):
        if status.get("error"):
            raise UploadError(f"Strava rejected the file: {status['error']}")
        if status.get("activity_id"):
            break
        time.sleep(poll_s)
        status = request("GET", f"{api}/uploads/{status['id']}", headers)
    activity = status.get("activity_id")
    return {
        "upload_id": status.get("id"),
        "activity_id": activity,
        "url": f"https://www.strava.com/activities/{activity}" if activity else None,
        "status": status.get("status", ""),
    }
