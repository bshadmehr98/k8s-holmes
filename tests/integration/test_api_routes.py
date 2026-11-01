"""The administrative and editing routes of the API, and the server's request parsing."""

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from k8sholmes.app import App
from k8sholmes.records import AuditEntry, Finding, Investigation
from k8sholmes.server import serve_in_thread
from tests.integration.test_server import Client


def raw_post(base: str, body: bytes) -> tuple[int, bytes]:
    """POST raw bytes to the session route: the status and the first words of the error."""
    request = urllib.request.Request(  # noqa: S310
        base + "/api/session", data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return response.status, b""
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        for needle in (b"not JSON", b"JSON object"):
            if needle in payload:
                return exc.code, needle
        return exc.code, payload[:40]


def admin_client(app: App, base: str) -> Client:
    app.auth.create_user("root", "a strong one", is_admin=True)
    client = Client(base)
    client.ok("POST", "/api/session", {"username": "root", "password": "a strong one"})
    return client


def test_environments_capabilities_knowledge_users_and_audit(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        admin = admin_client(app, base)
        # environments
        added = admin.ok("POST", "/api/environments", {"name": "staging", "context": "kind-staging"})[
            "environment"
        ]
        assert added["context"] == "kind-staging" and added["connected_at"] is None
        status, payload = admin.call("POST", "/api/environments", {"name": "staging", "context": "x"})
        assert status == 400 and "exists" in payload["error"]
        connected = admin.ok(
            "POST", f"/api/environments/{env['id'] if isinstance(env, dict) else env.id}/connect"
        )["environment"]
        assert connected["server_version"] == "v1.36.0"
        inventory = admin.ok("POST", f"/api/environments/{env.id}/inventory")["inventory"]
        assert inventory["items"] > 10 and inventory["relationships"] > 0
        settings = admin.ok(
            "PATCH",
            f"/api/environments/{env.id}/settings",
            {
                "probes_enabled": True,
                "changes_authorized_by": "operator_role",
                "retention": {"short_evidence_days": 7, "investigation_days": 30},
                "ignored": 1,
            },
        )["environment"]["settings"]
        assert (
            settings["probes_enabled"] is True
            and settings["changes_authorized_by"] == "operator_role"
            and settings["retention"]["short_evidence_days"] == 7
        )
        status, payload = admin.call(
            "PATCH", f"/api/environments/{env.id}/settings", {"changes_authorized_by": "anyone"}
        )
        assert status in (400, 500)  # pydantic refuses the value; the API reports it
        # capabilities
        cap = admin.ok(
            "PATCH",
            f"/api/environments/{env.id}/capabilities/k8s.logs",
            {
                "enabled": True,
                "namespaces": ["shop"],
                "max_items": 100,
                "max_log_bytes": 1024,
                "timeout_ms": 1000,
            },
        )["settings"]
        assert cap["namespaces"] == ["shop"]
        listed = admin.ok("GET", f"/api/capabilities?env={env.id}")["capabilities"]
        logs = next(c for c in listed if c["id"] == "k8s.logs")
        assert logs["settings"]["max_log_bytes"] == 1024 and logs["available"] is True
        probe = next(c for c in listed if c["id"] == "http.probe_url")
        assert probe["available"] is True  # probes were enabled above and root is an admin
        tested = admin.ok(
            "POST",
            "/api/capabilities/k8s.get/test",
            {"environment_id": env.id, "inputs": {"kind": "deployment", "name": "api", "namespace": "shop"}},
        )
        assert tested["call"]["status"] == "completed" and tested["evidence"]["coverage"] == "observed"
        refused = admin.ok(
            "POST",
            "/api/capabilities/k8s.logs/test",
            {"environment_id": env.id, "inputs": {"pod": "x", "namespace": "other"}},
        )
        assert refused["call"]["status"] == "refused" and "limited to shop" in refused["call"]["reason"]
        history = admin.ok("GET", f"/api/capabilities/k8s.get/calls?env={env.id}")["calls"]
        assert history and history[0]["capability_id"] == "k8s.get"
        status, _ = admin.call("GET", "/api/capabilities/k8s.nope/calls?env=" + env.id)
        assert status == 200  # an unknown id simply has no calls
        status, payload = admin.call(
            "POST", "/api/capabilities/k8s.nope/test", {"environment_id": env.id, "inputs": {}}
        )
        assert status == 404
        # knowledge
        note = admin.ok(
            "POST",
            "/api/knowledge",
            {
                "environment_id": env.id,
                "kind": "ownership",
                "text": "team shop owns api",
                "scope": {"kind": "Deployment", "namespace": "shop", "name": "api"},
            },
        )["knowledge"]
        edited = admin.ok("PATCH", f"/api/knowledge/{note['id']}", {"text": "team checkout owns api"})[
            "knowledge"
        ]
        assert edited["text"] == "team checkout owns api" and edited["revisions"][0]["before"] == {
            "text": "team shop owns api"
        }
        status, payload = admin.call(
            "POST", "/api/knowledge", {"environment_id": env.id, "kind": "ownership", "text": "  "}
        )
        assert status == 400
        # users and grants
        user = admin.ok("POST", "/api/users", {"username": "sam", "password": "a strong one"})["user"]
        granted = admin.ok(
            "PUT", f"/api/users/{user['id']}/grants", {"environment_id": env.id, "role": "investigator"}
        )["user"]
        assert granted["grants"] == [{"environment_id": env.id, "role": "investigator"}]
        status, payload = admin.call(
            "PUT", f"/api/users/{user['id']}/grants", {"environment_id": env.id, "role": "king"}
        )
        assert status == 400
        users = admin.ok("GET", "/api/users")["users"]
        assert {u["username"] for u in users} >= {"demo", "root", "sam"} and all(
            "password_hash" not in u for u in users
        )
        # models
        models = admin.ok("GET", "/api/models")["endpoints"]
        demo_model = next(m for m in models if m["id"] == "mdl_demo")
        assert demo_model["is_default"] is True
        status, payload = admin.call("POST", f"/api/models/{demo_model['id']}/test")
        assert status == 400 and "demo investigator" in payload["error"]
        status, payload = admin.call(
            "POST", "/api/models", {"name": "bad", "base_url": "ftp://x", "model": "m"}
        )
        assert status == 400
        added_model = admin.ok(
            "POST",
            "/api/models",
            {"name": "gw", "base_url": "https://gw.example/v1", "model": "m", "api_key_env": "GW_KEY"},
        )["endpoint"]
        assert added_model["has_key"] is False and added_model["is_default"] is False
        made_default = admin.ok(
            "PATCH", f"/api/models/{added_model['id']}", {"is_default": True, "limits": {"max_tokens": 10}}
        )["endpoint"]
        assert made_default["is_default"] is True and made_default["limits"]["max_tokens"] == 10
        assert (
            next(m for m in admin.ok("GET", "/api/models")["endpoints"] if m["id"] == "mdl_demo")[
                "is_default"
            ]
            is False
        )
        # audit
        entries = admin.ok("GET", "/api/audit")["entries"]
        actions = {e["action"] for e in entries}
        assert {
            "environment_created",
            "environment_connected",
            "environment_settings_changed",
            "capability_settings_changed",
            "capability_tested",
            "call_refused",
            "knowledge_added",
            "user_created",
            "grant_changed",
            "model_endpoint_added",
            "model_endpoint_changed",
        } <= actions
        assert all(e["user"] == "root" for e in entries if e["action"] == "user_created")
        scoped = admin.ok("GET", f"/api/audit?env={env.id}")["entries"]
        assert all(e["environment_id"] == env.id for e in scoped)
    finally:
        server.shutdown()
        server.server_close()


def test_investigation_control_routes_and_request_parsing(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base)
        client.ok("POST", "/api/session", {"username": "demo", "password": "demo-password"})
        status, payload = client.call(
            "POST", "/api/investigations", {"environment_id": env.id, "question": "  "}
        )
        assert status == 409 and "empty" in payload["error"]
        inv = client.ok(
            "POST",
            "/api/investigations",
            {"environment_id": env.id, "question": "Why is api restarting?", "idempotency_key": "c1"},
        )["investigation"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and client.ok("GET", f"/api/investigations/{inv['id']}")["running"]:
            time.sleep(0.05)
        status, payload = client.call("POST", f"/api/investigations/{inv['id']}/cancel")
        assert status == 409  # finished: nothing to cancel
        status, payload = client.call("POST", f"/api/investigations/{inv['id']}/resume")
        assert status == 409  # completed is not resumable
        status, payload = client.call("POST", f"/api/investigations/{inv['id']}/follow_up", {"message": " "})
        assert status == 400
        stored = app.store.get(Investigation, inv["id"])
        app.store.put(stored.model_copy(update={"status": "cancelled"}))
        resumed = client.ok("POST", f"/api/investigations/{inv['id']}/resume")
        assert resumed["investigation"]["status"] in ("cancelled", "running", "completed", "partial")
        while time.monotonic() < deadline and client.ok("GET", f"/api/investigations/{inv['id']}")["running"]:
            time.sleep(0.05)
        findings = client.ok("GET", f"/api/investigations/{inv['id']}")["findings"]
        multi = Finding(
            investigation_id=inv["id"],
            environment_id=env.id,
            title="t",
            explanation="e",
            condition="custom.two",
            resources=[],
            evidence_for=[],
            author="user:x",
        )
        app.store.put(multi)
        status, payload = client.call("POST", f"/api/findings/{multi.id}/accept", {"explanation": "x"})
        assert status == 400 and "exactly one resource" in payload["error"]
        status, payload = client.call("PATCH", f"/api/findings/{findings[0]['id']}", {"state": "verified"})
        assert status == 409  # open -> verified is not a transition
        status, payload = client.call(
            "PATCH", f"/api/findings/{findings[0]['id']}", {"edits": {"condition": "x"}}
        )
        assert status == 409 and "not edited by hand" in payload["error"]
        # request parsing
        status, payload = client.call("GET", "/api/nothing/here")
        assert status == 404
        status, payload = client.call("DELETE", "/api/me")
        assert status == 405
        assert raw_post(base, b"not json") == (400, b"not JSON")
        assert raw_post(base, b"[1]") == (400, b"JSON object")
        head = urllib.request.Request(base + "/", method="HEAD")  # noqa: S310
        with urllib.request.urlopen(head) as response:  # noqa: S310
            assert response.status == 200 and response.read() == b""
        status, _ = client.call("POST", "/tokens.css")
        assert status == 405
        assert any(a.action == "investigation_resumed" for a in app.store.find(AuditEntry))
    finally:
        server.shutdown()
        server.server_close()


def test_cli_setup_and_serve_commands(tmp_path: Path, capsys, monkeypatch: Any) -> None:
    from k8sholmes import cli

    home = tmp_path / "home"
    assert cli.main(["--home", str(home), "setup"]) == 0
    out = capsys.readouterr().out
    assert "#/?token=" in out and (home / "setup-token").exists()
    App.open(home, extensions=False).auth.create_user("root", "a strong one", is_admin=True)
    assert cli.main(["--home", str(home), "setup"]) == 1
    served: list[dict[str, Any]] = []
    import k8sholmes.server as server_module

    monkeypatch.setattr(server_module, "serve", lambda app, **kwargs: served.append(kwargs))
    assert cli.main(["--home", str(home), "serve", "--port", "0"]) == 0
    assert served == [{"host": "127.0.0.1", "port": 0, "allow_remote": False}]
