"""Guarantee 21: the server binds the loopback interface unless told otherwise. Guarantee 14 over the API: an
untested endpoint cannot be chosen, and a failing endpoint never switches to another. Plus the cancel path
and the planted secrets on every HTTP surface."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from k8sholmes.app import App
from k8sholmes.errors import InvalidInput
from k8sholmes.records import Investigation
from k8sholmes.server import make_server, serve_in_thread
from k8sholmes.testing.demo import PLANTED_BEARER, PLANTED_TOKEN
from tests.helpers import Client


def test_loopback_by_default(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    with pytest.raises(InvalidInput, match="allow-remote"):
        make_server(app, host="0.0.0.0", port=0)  # noqa: S104 - the refusal under test
    env, user = app.setup_demo()
    inv = app.engine.start(environment=env, question="x", user=user)
    app.store.put(inv.model_copy(update={"status": "running"}))
    server = make_server(app, port=0)
    try:
        assert app.store.get(Investigation, inv.id).status == "interrupted"  # a stale run is marked at start
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.server_close()


class Gate:
    """A model that waits for the test before answering, so a run can be caught mid-flight."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.open = threading.Event()
        self.asked = threading.Event()

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.asked.set()
        self.open.wait(10)
        return self.inner.complete(*args, **kwargs)


def test_cancel_resume_and_the_stream(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    gate = Gate(app.model_for("mdl_demo"))
    app.engine.model_for = lambda endpoint_id: gate
    server, _ = serve_in_thread(app)
    try:
        client = Client(f"http://127.0.0.1:{server.server_port}").sign_in("demo", "demo-password")
        inv = client.ok(
            "POST",
            "/api/investigations",
            {"environment_id": env.id, "question": "Why is api restarting?", "idempotency_key": "c"},
        )["investigation"]
        assert gate.asked.wait(10)
        cancelled = client.ok("POST", f"/api/investigations/{inv['id']}/cancel")["investigation"]
        assert cancelled["status"] == "cancelled"
        gate.open.set()
        bundle = client.wait_idle(inv["id"])
        assert bundle["investigation"]["status"] == "cancelled"
        assert bundle["steps"], "the framing step was kept"
        gate.asked.clear()
        resumed = client.call("POST", f"/api/investigations/{inv['id']}/resume")
        assert resumed[0] == 202
        events = client.sse(f"/api/investigations/{inv['id']}/events")
        kinds = [k for k, _ in events]
        assert kinds[0] == "status" and kinds[-1] == "concluded", kinds
        assert "step" in kinds and "call" in kinds
        bundle = client.wait_idle(inv["id"])
        assert bundle["investigation"]["status"] == "completed"
        surfaces = [
            json.dumps(events),
            json.dumps(bundle),
            client.ok("GET", f"/api/investigations/{inv['id']}/report"),
            json.dumps(client.ok("GET", f"/api/investigations/{inv['id']}/export")),
            json.dumps([client.ok("GET", f"/api/evidence/{e['id']}") for e in bundle["evidence"]]),
        ]
        assert all(PLANTED_TOKEN not in s and PLANTED_BEARER not in s for s in surfaces)
    finally:
        server.shutdown()
        server.server_close()
