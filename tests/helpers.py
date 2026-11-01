"""Helpers shared by the suites."""

import socket


def closed_port() -> int:
    """A loopback port nothing listens on: bound, read, released."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


import json  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from http.cookiejar import CookieJar  # noqa: E402
from typing import Any  # noqa: E402


class Client:
    """A browser-like client of the API: a cookie jar, JSON in and out, a reader for the event stream."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                is_json = response.headers.get("Content-Type", "").startswith("application/json")
                return response.status, (json.loads(raw) if is_json else raw.decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw.decode()

    def ok(self, method: str, path: str, body: Any = None) -> Any:
        status, payload = self.call(method, path, body)
        assert status < 300, (status, payload)
        return payload

    def sign_in(self, username: str, password: str) -> "Client":
        self.ok("POST", "/api/session", {"username": username, "password": password})
        return self

    def wait_idle(self, investigation_id: str, timeout: float = 30) -> Any:
        """Poll until the investigation's background thread is done; the final bundle."""
        deadline = time.monotonic() + timeout
        bundle = self.ok("GET", f"/api/investigations/{investigation_id}")
        while time.monotonic() < deadline and bundle["running"]:
            time.sleep(0.05)
            bundle = self.ok("GET", f"/api/investigations/{investigation_id}")
        return bundle

    def sse(self, path: str, limit: int = 200, timeout: float = 30) -> list[tuple[str, dict[str, Any]]]:
        request = urllib.request.Request(self.base + path)  # noqa: S310
        events: list[tuple[str, dict[str, Any]]] = []
        with self.opener.open(request, timeout=timeout) as response:
            kind = ""
            deadline = time.monotonic() + timeout
            while len(events) < limit and time.monotonic() < deadline:
                raw = response.readline()
                if not raw:
                    break  # the server ended the stream
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    events.append((kind, json.loads(line[5:])))
                    if kind in ("concluded", "failed"):
                        break
        return events
