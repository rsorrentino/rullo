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

"""Strava and intervals.icu upload tests against a local fake API."""

import time
from pathlib import Path

import pytest

from conftest import FakeApi
from rullo.config import Settings
from rullo.export import intervals, strava
from rullo.export.http import UploadError


def _fit(tmp_path: Path) -> Path:
    path = tmp_path / "ride.fit"
    path.write_bytes(b"\x0e\x20FITDATA")
    return path


def test_authorize_url() -> None:
    url = strava.authorize_url("123", "http://127.0.0.1:8765/cb", "xyz")
    assert url.startswith("https://www.strava.com/oauth/authorize?")
    assert "client_id=123" in url and "state=xyz" in url
    assert "scope=activity%3Awrite%2Cread" in url


def test_exchange_code_stores_tokens(fake_api: FakeApi) -> None:
    fake_api.reply(
        "POST",
        "/oauth/token",
        200,
        {
            "access_token": "A",
            "refresh_token": "R",
            "expires_at": 2_000_000_000,
            "athlete": {"firstname": "Ada", "lastname": "L"},
        },
    )
    s = Settings(strava_client_id="1", strava_client_secret="S")
    strava.exchange_code(s, "CODE", base=fake_api.url)
    assert (s.strava_access_token, s.strava_refresh_token) == ("A", "R")
    assert s.strava_athlete == "Ada L"
    assert b"code=CODE" in fake_api.seen[0].body
    assert b"grant_type=authorization_code" in fake_api.seen[0].body


def test_upload_refreshes_token_and_polls(fake_api: FakeApi, tmp_path: Path) -> None:
    fake_api.reply(
        "POST",
        "/oauth/token",
        200,
        {
            "access_token": "NEW",
            "refresh_token": "R2",
            "expires_at": int(time.time()) + 3600,
        },
    )
    fake_api.reply("POST", "/uploads", 201, {"id": 7, "status": "processing"})
    fake_api.reply("GET", "/uploads/7", 200, {"id": 7, "status": "processing"})
    fake_api.reply(
        "GET", "/uploads/7", 200, {"id": 7, "activity_id": 99, "status": "ready"}
    )
    s = Settings(
        strava_client_id="1",
        strava_client_secret="S",
        strava_refresh_token="R",
        strava_expires_at=0,
    )
    result = strava.upload(
        s,
        _fit(tmp_path),
        "Ride",
        "rullo-1",
        api=fake_api.url,
        auth=fake_api.url,
        poll_s=0.0,
    )
    assert result["activity_id"] == 99
    assert result["url"] == "https://www.strava.com/activities/99"
    assert s.strava_refresh_token == "R2"
    upload = next(r for r in fake_api.seen if r.path == "/uploads")
    assert upload.headers["Authorization"] == "Bearer NEW"
    assert b'name="data_type"\r\n\r\nfit' in upload.body
    assert b"FITDATA" in upload.body


def test_upload_reports_strava_errors(fake_api: FakeApi, tmp_path: Path) -> None:
    fake_api.reply(
        "POST", "/uploads", 201, {"id": 8, "error": "duplicate of activity 5"}
    )
    s = Settings(
        strava_refresh_token="R",
        strava_access_token="A",
        strava_expires_at=int(time.time()) + 3600,
    )
    with pytest.raises(UploadError, match="duplicate"):
        strava.upload(s, _fit(tmp_path), "Ride", "x", api=fake_api.url, poll_s=0.0)


def test_upload_needs_a_connection(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="not connected"):
        strava.upload(Settings(), _fit(tmp_path), "Ride", "x")


def test_intervals_upload(fake_api: FakeApi, tmp_path: Path) -> None:
    fake_api.reply("POST", "/athlete/0/activities", 200, {"id": "i123"})
    s = Settings(intervals_api_key="KEY")
    result = intervals.upload(s, _fit(tmp_path), "Ride", "x", api=fake_api.url)
    assert result["url"] == "https://intervals.icu/activities/i123"
    sent = fake_api.seen[0]
    assert sent.headers["Authorization"] == "Basic QVBJX0tFWTpLRVk="  # API_KEY:KEY
    assert b"FITDATA" in sent.body


def test_http_errors_become_upload_errors(fake_api: FakeApi, tmp_path: Path) -> None:
    fake_api.reply("POST", "/athlete/0/activities", 401, {"error": "bad key"})
    with pytest.raises(UploadError, match="401"):
        intervals.upload(
            Settings(intervals_api_key="K"), _fit(tmp_path), "R", "x", api=fake_api.url
        )
