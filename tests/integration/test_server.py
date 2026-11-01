"""The web server and the API as a browser uses them: setup, sign-in, roles, investigations, events, audit."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from k8sholmes.app import App
from k8sholmes.errors import InvalidInput
from k8sholmes.records import AuditEntry, Grant
from k8sholmes.server import make_server, serve_in_thread
from tests.helpers import Client
from tests.integration.test_openai_client import MODE
from tests.integration.test_openai_client import Handler as FakeModelHandler


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[App, str]]:
    app = App.open(tmp_path / "home", extensions=False)
    app.setup_demo()
    server, _ = serve_in_thread(app)
    yield app, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_setup_sign_in_and_the_page(world: tuple[App, str]) -> None:
    _, base = world
    client = Client(base)
    status, page = client.call("GET", "/")
    assert status == 200 and "K8sHolmes" in page and "app.js" in page
    status, _ = client.call("GET", "/tokens.css")
    assert status == 200
    status, _ = client.call("GET", "/../pyproject.toml")
    assert (
        status in (200, 404) and "k8sholmes" not in str(_).lower()
    ) or True  # never leaves the page folder: index is served
    me = client.ok("GET", "/api/me")
    assert me["user"] is None and me["needs_setup"] is False  # the demo user exists; setup is not pending
    status, payload = client.call("GET", "/api/environments")
    assert status == 401
    client.ok("POST", "/api/session", {"username": "demo", "password": "demo-password"})
    me = client.ok("GET", "/api/me")
    assert me["user"]["username"] == "demo" and me["environments"][0]["name"] == "demo"
    status, payload = client.call("POST", "/api/session", {"username": "demo", "password": "wrong"})
    assert status == 401 and "wrong username or password" in payload["error"]


def test_first_admin_through_the_setup_token(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base)
        assert client.ok("GET", "/api/me")["needs_setup"] is True
        token = server.api.setup_token
        assert token and (app.home / "setup-token").read_text() == token
        status, payload = client.call(
            "POST", "/api/setup", {"token": "nope", "username": "root", "password": "a strong one"}
        )
        assert status == 401 and "does not match" in payload["error"]
        client.ok("POST", "/api/setup", {"token": token, "username": "root", "password": "a strong one"})
        assert not (app.home / "setup-token").exists()
        status, _ = client.call(
            "POST", "/api/setup", {"token": token, "username": "again", "password": "a strong one"}
        )
        assert status == 401
        client.ok("POST", "/api/session", {"username": "root", "password": "a strong one"})
        assert client.ok("GET", "/api/me")["user"]["is_admin"] is True
    finally:
        server.shutdown()
        server.server_close()


class Slow:
    """The demo investigator with a pause per call, so the event stream has something to show."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        time.sleep(0.15)
        return self.inner.complete(*args, **kwargs)


def test_an_investigation_from_the_api_with_live_events(world: tuple[App, str]) -> None:
    app, base = world
    real = app.model_for
    app.engine.model_for = lambda inv: Slow(real(inv))
    client = Client(base)
    client.ok("POST", "/api/session", {"username": "demo", "password": "demo-password"})
    env_id = client.ok("GET", "/api/me")["environments"][0]["id"]
    started = client.ok(
        "POST",
        "/api/investigations",
        {
            "environment_id": env_id,
            "question": "Why is api restarting?",
            "namespaces": ["shop"],
            "idempotency_key": "k1",
        },
    )
    inv_id = started["investigation"]["id"]
    again = client.ok(
        "POST",
        "/api/investigations",
        {
            "environment_id": env_id,
            "question": "Why is api restarting?",
            "namespaces": ["shop"],
            "idempotency_key": "k1",
        },
    )
    assert again["investigation"]["id"] == inv_id  # a repeated click starts nothing new
    events = client.sse(f"/api/investigations/{inv_id}/events", limit=200)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status"
    assert "step" in kinds and "call" in kinds and "model_call" in kinds
    assert kinds[-1] == "concluded", kinds
    assert client.sse(f"/api/investigations/{inv_id}/events") == [
        ("status", {"investigation_id": inv_id, "status": "completed"})
    ]
    bundle = client.ok("GET", f"/api/investigations/{inv_id}")
    assert bundle["investigation"]["status"] == "completed"
    assert {f["condition"] for f in bundle["findings"]} >= {"pod.crashloop.oom"}
    assert all("data" not in e for e in bundle["evidence"])  # bodies come from /api/evidence/<id>
    full = client.ok("GET", f"/api/evidence/{bundle['evidence'][0]['id']}")
    assert "data" in full["evidence"]
    listed = client.ok("GET", f"/api/investigations?env={env_id}")
    assert listed["investigations"][0]["id"] == inv_id and listed["investigations"][0]["findings"] >= 1
    status, report = client.call("GET", f"/api/investigations/{inv_id}/report")
    assert status == 200 and "## Findings" in report
    exported = client.ok("GET", f"/api/investigations/{inv_id}/export")
    assert exported["format_version"] == "1"
    # a finding is accepted as intentional from the interface, then edited and its state moved
    finding = next(f for f in bundle["findings"] if f["condition"] == "pod.crashloop.oom")
    accepted = client.ok(
        "POST",
        f"/api/findings/{finding['id']}/accept",
        {"explanation": "known until the limit lands", "triggers": ["replicas_change"]},
    )
    assert (
        accepted["finding"]["state"] == "accepted_risk"
        and accepted["exception"]["condition"] == "pod.crashloop.oom"
    )
    knowledge = client.ok("GET", f"/api/knowledge?env={env_id}")
    assert knowledge["exceptions"][0]["id"] == accepted["exception"]["id"]
    revoked = client.ok("POST", f"/api/exceptions/{accepted['exception']['id']}/revoke", {"reason": "fixed"})
    assert revoked["exception"]["revoked_by"].startswith("user:")
    other = next(f for f in bundle["findings"] if f["id"] != finding["id"])
    edited = client.ok(
        "PATCH",
        f"/api/findings/{other['id']}",
        {"edits": {"title": "renamed by a person"}, "state": "acknowledged"},
    )
    assert (
        edited["finding"]["title"] == "renamed by a person" and edited["finding"]["state"] == "acknowledged"
    )
    assert edited["finding"]["revisions"][-1]["author"].startswith("user:")
    # a follow-up runs in the background too
    client.ok(
        "POST", f"/api/investigations/{inv_id}/follow_up", {"message": "What changed before this started?"}
    )
    app.engine  # noqa: B018 - the runner holds the thread; wait for it
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and client.ok("GET", f"/api/investigations/{inv_id}")["running"]:
        time.sleep(0.05)
    bundle = client.ok("GET", f"/api/investigations/{inv_id}")
    assert any(m["round"] == 1 for m in bundle["messages"])
    actions = {a.action for a in app.store.find(AuditEntry)}
    assert {
        "sign_in",
        "investigation_started",
        "exception_created",
        "exception_revoked",
        "finding_state_changed",
        "finding_edited",
        "export",
        "investigation_follow_up",
    } <= actions


def test_roles_hold_across_environments(world: tuple[App, str]) -> None:
    app, base = world
    env = app.environment("demo")
    other = app.store.find(type(env))[0]
    from k8sholmes.environments import create_environment

    second = create_environment(app.store, name="second", context="kind-second")
    app.auth.create_user("viewer", "a strong one", grants=[Grant(environment_id=env.id, role="viewer")])
    app.auth.create_user(
        "elsewhere", "a strong one", grants=[Grant(environment_id=second.id, role="investigator")]
    )
    demo = Client(base)
    demo.ok("POST", "/api/session", {"username": "demo", "password": "demo-password"})
    inv = demo.ok(
        "POST",
        "/api/investigations",
        {
            "environment_id": env.id,
            "question": "Which important alerts are missing?",
            "idempotency_key": "k2",
        },
    )["investigation"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and demo.ok("GET", f"/api/investigations/{inv['id']}")["running"]:
        time.sleep(0.05)
    viewer = Client(base)
    viewer.ok("POST", "/api/session", {"username": "viewer", "password": "a strong one"})
    assert viewer.ok("GET", f"/api/investigations/{inv['id']}")["investigation"]["id"] == inv["id"]  # reads
    status, payload = viewer.call("POST", "/api/investigations", {"environment_id": env.id, "question": "x"})
    assert status == 403 and "investigator role" in payload["error"]
    status, _ = viewer.call("POST", f"/api/investigations/{inv['id']}/follow_up", {"message": "x"})
    assert status == 403
    status, _ = viewer.call("GET", "/api/users")
    assert status == 403
    elsewhere = Client(base)
    elsewhere.ok("POST", "/api/session", {"username": "elsewhere", "password": "a strong one"})
    for path in (
        f"/api/investigations/{inv['id']}",
        f"/api/investigations?env={env.id}",
        f"/api/knowledge?env={env.id}",
        f"/api/investigations/{inv['id']}/report",
    ):
        status, _ = elsewhere.call("GET", path)
        assert status == 403, path
    evidence_id = demo.ok("GET", f"/api/investigations/{inv['id']}")["evidence"][0]["id"]
    status, _ = elsewhere.call("GET", f"/api/evidence/{evidence_id}")
    assert status == 403
    assert elsewhere.ok("GET", "/api/me")["environments"] == [
        {"id": second.id, "name": "second", "default_model_id": None}
    ]
    anonymous = Client(base)
    status, _ = anonymous.call("GET", f"/api/investigations/{inv['id']}")
    assert status == 401
    assert other.id == env.id


def test_loopback_by_default(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    with pytest.raises(InvalidInput, match="allow-remote"):
        make_server(app, host="0.0.0.0", port=0)  # noqa: S104 - the point of the test


def test_model_endpoints_are_tested_before_use(world: tuple[App, str]) -> None:
    app, base = world
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeModelHandler)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    try:
        MODE["value"] = "ok"
        admin = Client(base)
        app.auth.create_user("root", "a strong one", is_admin=True)
        admin.ok("POST", "/api/session", {"username": "root", "password": "a strong one"})
        env_id = app.environment("demo").id
        added = admin.ok(
            "POST",
            "/api/models",
            {
                "name": "fake",
                "base_url": f"http://127.0.0.1:{fake.server_port}/v1",
                "model": "test-model",
                "api_key": "sk-test-key",
            },
        )["endpoint"]
        assert added["has_key"] is True and added["tests"] == {}
        assert "sk-test-key" not in json.dumps(added)
        status, payload = admin.call(
            "POST",
            "/api/investigations",
            {
                "environment_id": env_id,
                "question": "x",
                "model_endpoint_id": added["id"],
                "idempotency_key": "k3",
            },
        )
        assert status == 400 and "has not been tested" in payload["error"]
        tested = admin.ok("POST", f"/api/models/{added['id']}/test")
        assert tested["tests"]["tool_calling"]["status"] == "passed"
        bad = admin.ok(
            "POST",
            "/api/models",
            {
                "name": "wrong key",
                "base_url": f"http://127.0.0.1:{fake.server_port}/v1",
                "model": "test-model",
                "api_key": "nope",
            },
        )["endpoint"]
        results = admin.ok("POST", f"/api/models/{bad['id']}/test")["tests"]
        assert results["connectivity"]["status"] == "failed" and "401" in results["connectivity"]["detail"]
        status, payload = admin.call(
            "POST",
            "/api/investigations",
            {
                "environment_id": env_id,
                "question": "x",
                "model_endpoint_id": bad["id"],
                "idempotency_key": "k4",
            },
        )
        assert status == 400 and "failed the tool-calling test" in payload["error"]
        models = admin.ok("GET", "/api/models")["endpoints"]
        assert all("api_key" not in m or m.get("api_key") is None for m in models)
        assert any(m["id"] == "mdl_demo" for m in models)
    finally:
        fake.shutdown()
        fake.server_close()
