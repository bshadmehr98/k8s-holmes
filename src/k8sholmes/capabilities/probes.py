"""The one probe (design §5.3, D-014): an HTTP request from the K8sHolmes host. Off until enabled."""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from k8sholmes.capabilities import Capability, Inputs
from k8sholmes.evidence import Result


class ProbeInputs(Inputs):
    url: str = Field(description="An http or https URL reachable from the K8sHolmes host.")
    timeout_ms: int = Field(default=5000, ge=100, le=60_000)


def _tls_expiry(host: str, port: int, timeout: float) -> str | None:
    context = ssl.create_default_context()
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=host) as tls,
    ):
        cert = cast(dict[str, Any], tls.getpeercert())
    not_after = cert.get("notAfter")
    if not isinstance(not_after, str):
        return None
    return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").isoformat()  # noqa: DTZ007 - the format carries GMT


def run_probe_url(inputs: BaseModel, ctx: object) -> Result:
    i = cast(ProbeInputs, inputs)
    parts = urlsplit(i.url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return Result(
            summary="not an http(s) URL",
            data=None,
            coverage="cannot_answer",
            coverage_reason="only http and https URLs are probed",
            error="not an http(s) URL",
        )
    timeout = i.timeout_ms / 1000
    started = time.monotonic()
    data: dict[str, Any] = {"url": i.url}
    try:
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
            i.url, method="GET", headers={"User-Agent": "K8sHolmes probe"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data["status"] = response.status
            data["headers"] = {
                k: v
                for k, v in response.headers.items()
                if k.lower() in ("server", "content-type", "date", "cache-control")
            }
    except urllib.error.HTTPError as exc:
        data["status"] = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        data["error"] = str(exc.reason if isinstance(exc, urllib.error.URLError) else exc)
    data["latency_ms"] = int((time.monotonic() - started) * 1000)
    if parts.scheme == "https":
        try:
            data["tls_expires"] = _tls_expiry(parts.hostname, parts.port or 443, timeout)
        except (OSError, ssl.SSLError, ValueError) as exc:
            data["tls_error"] = str(exc)
    if "error" in data:
        return Result(
            summary=f"{i.url} unreachable: {data['error']}",
            data=data,
            coverage="observed",
            coverage_reason="the request from the K8sHolmes host failed",
            error=str(data["error"]),
        )
    return Result(
        summary=f"{i.url}: HTTP {data.get('status')} in {data['latency_ms']} ms",
        data=data,
        coverage="observed",
        coverage_reason="one request from the K8sHolmes host, not from inside the cluster",
    )


CAPABILITIES: list[Capability] = [
    Capability(
        id="http.probe_url",
        title="Probe a URL",
        purpose=(
            "One HTTP request from the K8sHolmes host to a URL: status, latency, TLS expiry. An active "
            "probe: it sends traffic. Says nothing about reachability from inside the cluster."
        ),
        class_="probe",
        systems=("network",),
        inputs=ProbeInputs,
        permissions=(),
        run=run_probe_url,
        coverage=("observed", "cannot_answer"),
    ),
]
