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

"""Minimal asyncio HTTP/1.1 server for the local app (stdlib only).

Only serves 127.0.0.1. Requests that change state must carry ``X-Rullo: 1``,
which a browser will not send cross-origin without a CORS preflight this server
never approves, and every request must name a local Host (DNS rebinding).
"""

import asyncio
import json
import logging
import urllib.parse
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_BODY = 20 * 1024 * 1024
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_REASONS = {
    200: "OK",
    302: "Found",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    500: "Internal Server Error",
    502: "Bad Gateway",
}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError as exc:
            raise HttpError(400, "body is not valid JSON") from exc


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Stream:
    """A Server-Sent Events response fed by an async iterator of frames."""

    frames: AsyncGenerator[bytes, None]


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def json_response(data: Any, status: int = 200) -> Response:
    return Response(status, json.dumps(data).encode())


def redirect(location: str) -> Response:
    return Response(302, b"", "text/plain", {"Location": location})


Handler = Callable[[Request], Awaitable[Response | Stream]]


async def _read_request(reader: asyncio.StreamReader) -> Request | None:
    line = await reader.readline()
    if not line:
        return None
    parts = line.decode("latin-1").split()
    if len(parts) != 3:
        raise HttpError(400, "malformed request line")
    method, target, _ = parts
    headers: dict[str, str] = {}
    while (raw := await reader.readline()).strip():
        name, _, value = raw.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length > MAX_BODY:
        raise HttpError(413, "upload too large")
    body = await reader.readexactly(length) if length else b""
    url = urllib.parse.urlsplit(target)
    query = dict(urllib.parse.parse_qsl(url.query))
    return Request(method.upper(), urllib.parse.unquote(url.path), query, headers, body)


def _check_origin(req: Request) -> None:
    host = req.headers.get("host", "")
    hostname = (
        host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    )
    if hostname not in _LOCAL_HOSTS:
        raise HttpError(403, "only local requests are allowed")
    if req.method not in ("GET", "HEAD") and req.headers.get("x-rullo") != "1":
        raise HttpError(403, "missing X-Rullo header")


def _head(
    status: int, content_type: str, extra: dict[str, str], length: int | None
) -> bytes:
    headers = {"Cache-Control": "no-store"} | extra  # a response may allow caching
    lines = [
        f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}",
        f"Content-Type: {content_type}",
        "X-Content-Type-Options: nosniff",
        "Connection: close",
    ]
    if length is not None:
        lines.append(f"Content-Length: {length}")
    lines += [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def serve_handler(
    handler: Handler,
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def on_connection(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                req = await _read_request(reader)
                if req is None:
                    return
                _check_origin(req)
                result = await handler(req)
            except HttpError as exc:
                result = json_response({"error": str(exc)}, exc.status)
            except Exception:
                logger.exception("request failed")
                result = json_response({"error": "internal error"}, 500)
            if isinstance(result, Stream):
                writer.write(_head(200, "text/event-stream", {}, None))
                await writer.drain()
                async with aclosing(result.frames) as frames:
                    async for frame in frames:
                        writer.write(frame)
                        await writer.drain()
            else:
                writer.write(
                    _head(
                        result.status,
                        result.content_type,
                        result.headers,
                        len(result.body),
                    )
                    + result.body
                )
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    return on_connection
