"""Guarantee 16: a viewer cannot start; another environment's investigator cannot read; an admin can.
Guarantee 17: the actions of §14.4 are audited. Both through the HTTP API alone."""

from pathlib import Path

import pytest

from k8sholmes.app import App
from k8sholmes.server import serve_in_thread
from tests.helpers import Client


@pytest.fixture
def served(tmp_path: Path):
    app = App.open(tmp_path / "home", extensions=False)
    app.setup_demo()
    app.auth.create_user("root", "a strong one", is_admin=True)
    server, _ = serve_in_thread(app)
    yield app, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_roles_hold_and_actions_are_audited(served) -> None:
    _, base = served
    root = Client(base).sign_in("root", "a strong one")
    demo_env = next(e for e in root.ok("GET", "/api/environments")["environments"] if e["name"] == "demo")
    second = root.ok("POST", "/api/environments", {"name": "second", "context": "kind-second"})["environment"]
    for name, env_id, role in (
        ("viewer", demo_env["id"], "viewer"),
        ("elsewhere", second["id"], "investigator"),
    ):
        made = root.ok("POST", "/api/users", {"username": name, "password": "a strong one"})["user"]
        root.ok("PUT", f"/api/users/{made['id']}/grants", {"environment_id": env_id, "role": role})
    demo = Client(base).sign_in("demo", "demo-password")
    inv = demo.ok(
        "POST",
        "/api/investigations",
        {
            "environment_id": demo_env["id"],
            "question": "Which important alerts are missing?",
            "idempotency_key": "k",
        },
    )["investigation"]
    bundle = demo.wait_idle(inv["id"])
    finding = bundle["findings"][0]
    evidence_id = bundle["evidence"][0]["id"]
    report = demo.ok(
        "POST",
        "/api/reports",
        {"environment_id": demo_env["id"], "kind": "investigation", "investigation_id": inv["id"]},
    )["report"]
    share = root.ok("POST", f"/api/reports/{report['id']}/shares", {"days": 1})
    proposal = demo.ok(
        "POST",
        "/api/proposals",
        {
            "investigation_id": inv["id"],
            "finding_ids": [finding["id"]],
            "kind": "task",
            "title": "t",
            "change": "c",
            "intended_effect": "e",
            "verification_condition": finding["condition"],
        },
    )["proposal"]
    saved = demo.ok(
        "POST",
        "/api/saved",
        {"environment_id": demo_env["id"], "question": "Which important alerts are missing?"},
    )["saved"]
    # the viewer reads and nothing more
    viewer = Client(base).sign_in("viewer", "a strong one")
    assert viewer.ok("GET", f"/api/reports/{report['id']}")["report"]["id"] == report["id"]
    assert viewer.ok("GET", f"/api/saved/{saved['id']}")["saved"]["id"] == saved["id"]
    assert viewer.ok("GET", f"/api/investigations/{inv['id']}")["investigation"]["id"] == inv["id"]
    writes = [
        ("POST", "/api/investigations", {"environment_id": demo_env["id"], "question": "x"}),
        ("POST", f"/api/investigations/{inv['id']}/follow_up", {"message": "x"}),
        ("POST", f"/api/investigations/{inv['id']}/cancel", None),
        ("POST", f"/api/investigations/{inv['id']}/resume", None),
        ("PATCH", f"/api/findings/{finding['id']}", {"state": "acknowledged"}),
        ("POST", f"/api/findings/{finding['id']}/accept", {"explanation": "x"}),
        ("POST", "/api/knowledge", {"environment_id": demo_env["id"], "kind": "lesson", "text": "x"}),
        ("POST", f"/api/environments/{demo_env['id']}/inventory", None),
        ("POST", "/api/capabilities/k8s.get/test", {"environment_id": demo_env["id"], "inputs": {}}),
        ("GET", f"/api/capabilities/k8s.get/calls?env={demo_env['id']}", None),
        (
            "POST",
            "/api/reports",
            {"environment_id": demo_env["id"], "kind": "investigation", "investigation_id": inv["id"]},
        ),
        ("POST", f"/api/reports/{report['id']}/edits", {"section": "The answer", "text": "x"}),
        ("POST", f"/api/reports/{report['id']}/shares", {"days": 1}),
        ("POST", f"/api/shares/{share['share']['id']}/revoke", None),
        ("POST", f"/api/investigations/{inv['id']}/compare_models", {"endpoint_ids": ["mdl_demo"]}),
        (
            "POST",
            "/api/proposals",
            {
                "investigation_id": inv["id"],
                "finding_ids": [finding["id"]],
                "kind": "task",
                "title": "t",
                "change": "c",
                "intended_effect": "e",
                "verification_condition": "x",
            },
        ),
        ("PATCH", f"/api/proposals/{proposal['id']}", {"state": "rejected"}),
        ("POST", f"/api/proposals/{proposal['id']}/verify", None),
        ("POST", "/api/saved", {"environment_id": demo_env["id"], "question": "x"}),
        ("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"enabled": False}}),
        ("POST", f"/api/saved/{saved['id']}/run", None),
    ]
    for method, path, body in writes:
        status, _ = viewer.call(method, path, body)
        assert status == 403, (method, path, status)
    # the investigator of another environment sees nothing of this one
    elsewhere = Client(base).sign_in("elsewhere", "a strong one")
    reads = [
        ("GET", f"/api/investigations/{inv['id']}", None),
        ("GET", f"/api/investigations?env={demo_env['id']}", None),
        ("GET", f"/api/investigations/{inv['id']}/events", None),
        ("GET", f"/api/investigations/{inv['id']}/report", None),
        ("GET", f"/api/investigations/{inv['id']}/export", None),
        ("GET", f"/api/evidence/{evidence_id}", None),
        ("GET", f"/api/knowledge?env={demo_env['id']}", None),
        ("GET", f"/api/capabilities?env={demo_env['id']}", None),
        ("PATCH", f"/api/findings/{finding['id']}", {"state": "acknowledged"}),
        ("POST", "/api/investigations", {"environment_id": demo_env["id"], "question": "x"}),
        ("GET", f"/api/reports?env={demo_env['id']}", None),
        ("GET", f"/api/reports/{report['id']}", None),
        ("GET", f"/api/reports/{report['id']}/markdown", None),
        ("GET", f"/api/reports/{report['id']}/export", None),
        ("POST", f"/api/reports/{report['id']}/edits", {"section": "The answer", "text": "x"}),
        ("POST", f"/api/shares/{share['share']['id']}/revoke", None),
        ("GET", f"/api/investigations/{inv['id']}/live", None),
        ("POST", f"/api/investigations/{inv['id']}/compare_models", {"endpoint_ids": ["mdl_demo"]}),
        ("GET", f"/api/proposals?env={demo_env['id']}", None),
        ("PATCH", f"/api/proposals/{proposal['id']}", {"state": "rejected"}),
        ("POST", f"/api/proposals/{proposal['id']}/verify", None),
        (
            "POST",
            "/api/proposals",
            {
                "investigation_id": inv["id"],
                "finding_ids": [finding["id"]],
                "kind": "task",
                "title": "t",
                "change": "c",
                "intended_effect": "e",
                "verification_condition": "x",
            },
        ),
        ("GET", f"/api/saved?env={demo_env['id']}", None),
        ("GET", f"/api/saved/{saved['id']}", None),
        ("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"enabled": False}}),
        ("POST", f"/api/saved/{saved['id']}/run", None),
    ]
    for method, path, body in reads:
        status, _ = elsewhere.call(method, path, body)
        assert status == 403, (method, path, status)
    assert [e["id"] for e in elsewhere.ok("GET", "/api/me")["environments"]] == [second["id"]]
    # an idempotency key is scoped: another user in another environment never gets this investigation
    status, payload = elsewhere.call(
        "POST",
        "/api/investigations",
        {"environment_id": second["id"], "question": "mine", "idempotency_key": "k"},
    )
    assert status in (
        201,
        400,
    )  # 400: the second environment has no reachable cluster, which is refused before anything starts
    if status == 201:
        assert (
            payload["investigation"]["id"] != inv["id"]
            and payload["investigation"]["environment_id"] == second["id"]
        )
    # admin-only routes refuse non-admins; anonymous requests get 401 everywhere
    for method, path, body in (
        ("GET", "/api/users", None),
        ("GET", "/api/audit", None),
        ("POST", "/api/models", {"name": "x", "base_url": "http://x/v1", "model": "m"}),
        ("POST", "/api/environments", {"name": "n", "context": "c"}),
        ("PATCH", f"/api/environments/{demo_env['id']}/settings", {"probes_enabled": True}),
        ("PATCH", f"/api/environments/{demo_env['id']}/capabilities/k8s.get", {"enabled": False}),
        (
            "POST",
            f"/api/reports/{report['id']}/shares",
            {"days": 1},
        ),  # shares are the administrator's (§14.1)
        ("POST", f"/api/shares/{share['share']['id']}/revoke", None),
    ):
        status, _ = demo.call(method, path, body)
        assert status == 403, (method, path)
    anonymous = Client(base)
    for method, path, body in (
        ("GET", f"/api/investigations/{inv['id']}", None),
        ("POST", "/api/investigations", {"environment_id": demo_env["id"], "question": "x"}),
        ("DELETE", "/api/session", None),
        ("GET", "/api/models", None),
        ("PATCH", f"/api/findings/{finding['id']}", {"state": "acknowledged"}),
        ("GET", f"/api/reports/{report['id']}", None),
        ("GET", f"/api/reports/{report['id']}/markdown", None),
        ("POST", f"/api/reports/{report['id']}/edits", None),
        ("POST", f"/api/reports/{report['id']}/shares", None),
        ("POST", f"/api/shares/{share['share']['id']}/revoke", None),
        ("GET", f"/api/investigations/{inv['id']}/live", None),
        ("POST", f"/api/investigations/{inv['id']}/compare_models", None),
        ("GET", f"/api/proposals?env={demo_env['id']}", None),
        ("POST", "/api/proposals", None),
        ("PATCH", f"/api/proposals/{proposal['id']}", None),
        ("POST", f"/api/proposals/{proposal['id']}/verify", None),
        ("GET", f"/api/saved?env={demo_env['id']}", None),
        ("POST", "/api/saved", None),
        ("GET", f"/api/saved/{saved['id']}", None),
        ("POST", f"/api/saved/{saved['id']}/run", None),
    ):
        status, _ = anonymous.call(method, path, body)
        assert status == 401, (method, path)
    # the one route that answers without a session opens one report and nothing else
    assert anonymous.ok("GET", f"/api/shared/{share['token']}")["report"]["id"] == report["id"]
    # the administrator reads every environment
    assert root.ok("GET", f"/api/investigations/{inv['id']}")["investigation"]["id"] == inv["id"]
    assert {e["name"] for e in root.ok("GET", "/api/environments")["environments"]} == {"demo", "second"}
    # guarantee 17: the audit log
    demo.ok("POST", f"/api/findings/{finding['id']}/accept", {"explanation": "intentional"})
    demo.ok("PATCH", f"/api/findings/{bundle['findings'][1]['id']}", {"state": "acknowledged"})
    exception_id = demo.ok("GET", f"/api/knowledge?env={demo_env['id']}")["exceptions"][0]["id"]
    demo.ok("POST", f"/api/exceptions/{exception_id}/revoke", {"reason": "done"})
    demo.ok("GET", f"/api/investigations/{inv['id']}/export")
    demo.ok(
        "POST",
        "/api/capabilities/http.probe_url/test",
        {"environment_id": demo_env["id"], "inputs": {"url": "http://127.0.0.1:1/"}},
    )  # refused: probes are off
    actions = {e["action"] for e in root.ok("GET", "/api/audit")["entries"]}
    assert {
        "sign_in",
        "sign_in_failed",
        "call_refused",
        "exception_created",
        "exception_revoked",
        "finding_state_changed",
        "export",
        "user_created",
        "grant_changed",
        "environment_created",
    } - actions == {"sign_in_failed"}
    status, _ = Client(base).call("POST", "/api/session", {"username": "root", "password": "nope"})
    assert status == 401
    assert "sign_in_failed" in {e["action"] for e in root.ok("GET", "/api/audit")["entries"]}
