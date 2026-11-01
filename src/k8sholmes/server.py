"""The web server (design §15, D-007): the standard library's threading HTTP server, the static page,
the JSON API and server-sent events. It binds the loopback interface unless told otherwise."""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from k8sholmes import saved as saved_mod
from k8sholmes.api import Api, Request, Response
from k8sholmes.app import App
from k8sholmes.errors import InvalidInput
from k8sholmes.records import Environment, Investigation, SavedInvestigation, User, now
from k8sholmes.runner import Runner

log = logging.getLogger("k8sholmes")
PAGE_DIR = Path(__file__).parent / "page"
MAX_BODY = 4 * 1024 * 1024
HEARTBEAT_S = 15.0
TERMINAL_EVENTS = ("concluded", "failed")


def static_file(path: str) -> tuple[bytes, str] | None:
    """A file of the interface, or None. Paths never leave the page folder."""
    target = (PAGE_DIR / (path.lstrip("/") or "index.html")).resolve()
    if not target.is_relative_to(PAGE_DIR.resolve()) or not target.is_file():
        return None
    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
        kind += "; charset=utf-8"
    return target.read_bytes(), kind


class Handler(BaseHTTPRequestHandler):
    """One request. `server.api` is the `Api`; `server.app` the `App`."""

    server: K8sHolmesServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s " + format, self.address_string(), *args)

    def _request(self) -> Request | None:
        parts = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        cookies: dict[str, str] = {}
        if self.headers.get("Cookie"):
            jar = SimpleCookie()
            jar.load(self.headers["Cookie"])
            cookies = {k: m.value for k, m in jar.items()}
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            if length > MAX_BODY:
                self._send(Response(413, {"error": "the body is too large"}))
                return None
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                self._send(Response(400, {"error": "the body is not JSON"}))
                return None
            if body is not None and not isinstance(body, dict):
                self._send(Response(400, {"error": "the body must be a JSON object"}))
                return None
        return Request(
            method=self.command,
            path=parts.path,
            query=query,
            body=cast(dict[str, Any] | None, body),
            cookies=cookies,
        )

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if not parts.path.startswith("/api/"):
            self._static(parts.path)
            return
        req = self._request()
        if req is None:
            return
        response = self.server.api.handle(req)
        if response.stream:
            self._stream(response.stream)
        else:
            self._send(response)

    do_HEAD = do_POST = do_PATCH = do_PUT = do_DELETE = do_GET  # noqa: N815 - the base class's names

    def _static(self, path: str) -> None:
        if self.command not in ("GET", "HEAD"):
            self._send(Response(405, {"error": "method not allowed"}))
            return
        found = static_file(path) or static_file("/index.html")  # the hash router owns every other path
        if found is None:
            self._send(Response(404, {"error": "the interface files are missing"}))
            return
        data, content_type = found
        self._send(Response(200, data, content_type=content_type, headers={"Cache-Control": "no-store"}))

    def _send(self, response: Response) -> None:
        payload = response.bytes() if response.body is not None else b""
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in response.headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _stream(self, investigation_id: str) -> None:
        """Server-sent events until the investigation stops running or the client goes away."""
        runner = self.server.runner
        q = runner.subscribe(investigation_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            status = self.server.app.store.get(Investigation, investigation_id).status
            self._write_event("status", {"investigation_id": investigation_id, "status": status})
            if not runner.is_running(investigation_id):
                return  # already over: the one status event is the whole stream
            while True:
                try:
                    kind, payload = q.get(timeout=HEARTBEAT_S)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    if not runner.is_running(investigation_id):
                        break
                    continue
                self._write_event(kind, payload)
                if kind in TERMINAL_EVENTS or (
                    kind == "status" and payload.get("status") in ("cancelled", "interrupted")
                ):
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            runner.unsubscribe(investigation_id, q)

    def _write_event(self, kind: str, payload: dict[str, Any]) -> None:
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n".encode())
        self.wfile.flush()


class K8sHolmesServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App, *, scheduler_interval_s: float = 60.0) -> None:
        super().__init__(address, Handler)
        self.app = app
        self.runner = Runner(app.engine)
        self.runner.on_finished = self._after_run
        self.api = Api(app, self.runner)
        self.scheduler = saved_mod.Scheduler(
            app.store, run_saved=self._run_saved, auth=app.auth, interval_s=scheduler_interval_s
        )
        self.scheduler.start()

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_port
        return f"http://{host}:{port}/"

    def server_close(self) -> None:
        self.scheduler.stop()
        super().server_close()

    def _run_saved(self, saved: SavedInvestigation) -> None:
        """A scheduled run acts as the person who saved the investigation (design §12)."""
        user = self.app.store.get(User, saved.created_by)
        env = self.app.store.get(Environment, saved.environment_id)
        inv = saved_mod.start_run(self.app.store, self.app.engine.start, saved, env, user)
        self.runner.run(inv.id, user)

    def _after_run(self, inv: Investigation) -> None:
        """After a saved investigation's run: the comparison by code, its report, the notification (§12)."""
        if not inv.saved_id or inv.status not in ("completed", "partial"):
            return
        saved = self.app.store.get(SavedInvestigation, inv.saved_id)
        comparison = saved_mod.compare_runs(self.app.store, saved, inv.id)
        report = saved_mod.comparison_report(self.app.store, saved, comparison, by="scheduler")
        self.runner.publish(
            "comparison", {"investigation_id": inv.id, "summary": comparison.summary, "report_id": report.id}
        )
        if saved_mod.notify(saved, comparison, f"{self.url}#/reports/{report.id}"):
            self.app.store.put(
                saved.model_copy(
                    update={"notification": saved.notification.model_copy(update={"last_sent_at": now()})}
                )
            )


def make_server(
    app: App,
    *,
    host: str = "127.0.0.1",
    port: int = 8800,
    allow_remote: bool = False,
    scheduler_interval_s: float = 60.0,
) -> K8sHolmesServer:
    """A server bound to the loopback interface unless `allow_remote` (design §15.5, guarantee 21).

    Investigations that were running when the last process stopped are marked interrupted (§16.2).
    """
    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        raise InvalidInput(
            f"binding {host} exposes K8sHolmes beyond this machine; pass --allow-remote and put TLS in front"
        )
    for inv in app.engine.mark_interrupted():
        log.warning("investigation %s was running when the server stopped; it is marked interrupted", inv.id)
    try:
        return K8sHolmesServer((host, port), app, scheduler_interval_s=scheduler_interval_s)
    except OSError as exc:
        raise InvalidInput(f"cannot listen on {host}:{port}: {exc}") from exc


def serve_in_thread(
    app: App, *, host: str = "127.0.0.1", port: int = 0
) -> tuple[K8sHolmesServer, threading.Thread]:
    """For tests: a server on a free port, serving in a daemon thread."""
    server = make_server(app, host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="k8sholmes-server")
    thread.start()
    return server, thread


__all__ = ["K8sHolmesServer", "make_server", "serve_in_thread", "static_file"]
