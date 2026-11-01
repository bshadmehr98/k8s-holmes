"""The report, proposal and saved-investigation routes (design §10 to §12), the public share route, the live
summary, the model comparison, and the server's scheduler with the comparison after a saved run."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from k8sholmes import saved as saved_mod
from k8sholmes.app import App, grant_role
from k8sholmes.records import AuditEntry, Investigation, Report, SavedInvestigation
from k8sholmes.server import make_server, serve_in_thread
from tests.helpers import Client


def investigated(client: Client, env_id: str, question: str = "Why is api restarting?") -> dict[str, Any]:
    inv = client.ok("POST", "/api/investigations", {"environment_id": env_id, "question": question})[
        "investigation"
    ]
    return client.wait_idle(inv["id"])


def test_reports_edits_shares_and_downloads(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    app.auth.create_user("root", "root-password", is_admin=True)
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base).sign_in("demo", "demo-password")
        root = Client(base).sign_in("root", "root-password")
        bundle = investigated(client, env.id)
        inv_id = bundle["investigation"]["id"]
        live = client.ok("GET", f"/api/investigations/{inv_id}/live")["live"]
        assert live["status"] == "completed" and live["steps"] == len(bundle["steps"]) and live["top_finding"]
        status, payload = client.call("POST", "/api/reports", {"environment_id": env.id, "kind": "weekly"})
        assert status == 400 and "kind is one of" in payload["error"]
        report = client.ok(
            "POST",
            "/api/reports",
            {"environment_id": env.id, "kind": "investigation", "investigation_id": inv_id},
        )["report"]
        assert report["revision"] == 1 and report["numbers"]["findings"] == len(bundle["findings"])
        incident = client.ok(
            "POST",
            "/api/reports",
            {"environment_id": env.id, "kind": "incident_review", "investigation_id": inv_id},
        )["report"]
        assert incident["kind"] == "incident_review"
        review = client.ok(
            "POST",
            "/api/reports",
            {"environment_id": env.id, "kind": "reliability_review", "namespaces": ["shop"]},
        )["report"]
        assert review["numbers"]["workloads"] > 0
        listed = client.ok("GET", f"/api/reports?env={env.id}")["reports"]
        assert {r["id"] for r in listed} == {report["id"], incident["id"], review["id"]}
        assert all("sections" not in r for r in listed)
        edited = client.ok(
            "POST",
            f"/api/reports/{report['id']}/edits",
            {"section": "The answer", "text": "Checked by hand."},
        )["report"]
        assert edited["edits"][0]["author"].startswith("user:")
        status, payload = client.call(
            "POST", f"/api/reports/{report['id']}/edits", {"section": "The answer", "text": "  "}
        )
        assert status == 400
        read = client.ok("GET", f"/api/reports/{report['id']}")
        assert read["report"]["edits"] and read["revisions"][0]["revision"] == 1 and read["shares"] == []
        status, markdown = client.call("GET", f"/api/reports/{report['id']}/markdown")
        assert (
            status == 200
            and markdown.startswith("# Why is api restarting?")
            and "> Edit by user:" in markdown
        )
        exported = client.ok("GET", f"/api/reports/{report['id']}/export")
        assert exported["format_version"] == "1" and exported["report"]["id"] == report["id"]
        # a share: an administrator mints it; the token appears once; the public route needs no session
        status, _ = client.call("POST", f"/api/reports/{report['id']}/shares", {"days": 3})
        assert status == 403  # the demo operator is not an administrator
        status, payload = root.call("POST", f"/api/reports/{report['id']}/shares", {"days": 400})
        assert status == 400
        shared = root.ok("POST", f"/api/reports/{report['id']}/shares", {"days": 3})
        token = shared["token"]
        assert token not in json.dumps(shared["share"]) and shared["path"] == f"/#/shared/{token}"
        assert all(token not in a.model_dump_json() for a in app.store.find(AuditEntry))
        assert token not in caplog.text
        anonymous = Client(base)
        public = anonymous.ok("GET", f"/api/shared/{token}")
        assert public["shared"] is True and public["report"]["id"] == report["id"]
        status, text = anonymous.call("GET", f"/api/shared/{token}/markdown")
        assert status == 200 and text.startswith("# ")
        status, _ = anonymous.call("GET", f"/api/reports/{report['id']}")
        assert status == 401  # the token opens one report, not the API
        status, _ = anonymous.call("GET", "/api/shared/nope")
        assert status == 404
        assert client.ok("GET", f"/api/reports/{report['id']}")["shares"][0]["id"] == shared["share"]["id"]
        assert token not in json.dumps(client.ok("GET", f"/api/reports/{report['id']}")["shares"])
        status, _ = client.call("POST", f"/api/shares/{shared['share']['id']}/revoke")
        assert status == 403
        root.ok("POST", f"/api/shares/{shared['share']['id']}/revoke")
        status, _ = anonymous.call("GET", f"/api/shared/{token}")
        assert status == 404
        actions = {a.action for a in app.store.find(AuditEntry)}
        assert {"report_generated", "report_edited", "share_created", "share_revoked", "export"} <= actions
        assert token not in caplog.text
        # a viewer reads but neither generates nor edits
        app.auth.create_user("reader", "reader-password")
        grant_role(app, "reader", env, "viewer")
        reader = Client(base).sign_in("reader", "reader-password")
        assert reader.ok("GET", f"/api/reports/{report['id']}")["report"]["id"] == report["id"]
        status, _ = reader.call(
            "POST",
            "/api/reports",
            {"environment_id": env.id, "kind": "investigation", "investigation_id": inv_id},
        )
        assert status == 403
        status, _ = reader.call(
            "POST", f"/api/reports/{report['id']}/edits", {"section": "The answer", "text": "x"}
        )
        assert status == 403
        # the fixed-evidence comparison runs the concluding phase per endpoint on the stored evidence
        status, payload = client.call(
            "POST", f"/api/investigations/{inv_id}/compare_models", {"endpoint_ids": []}
        )
        assert status == 400
        compared = client.ok(
            "POST",
            f"/api/investigations/{inv_id}/compare_models",
            {"endpoint_ids": ["mdl_demo", "mdl_missing"]},
        )
        assert compared["kind"] == "fixed_evidence" and len(compared["stored_findings"]) == len(
            bundle["findings"]
        )
        result, missing = compared["results"]
        assert result["model"] and result["findings"] and result["usage"]["prompt_tokens"] >= 0
        assert (
            missing["endpoint_id"] == "mdl_missing" and "error" in missing
        )  # one failure never stops the others
        assert len(client.ok("GET", f"/api/investigations/{inv_id}")["findings"]) == len(
            bundle["findings"]
        )  # nothing stored
        running = app.store.get(Investigation, inv_id)
        app.store.put(running.model_copy(update={"status": "running"}))
        status, _ = client.call(
            "POST", f"/api/investigations/{inv_id}/compare_models", {"endpoint_ids": ["mdl_demo"]}
        )
        assert status == 409  # the evidence of a running investigation is still changing
        app.store.put(running)
    finally:
        server.shutdown()
        server.server_close()


def test_proposals_from_the_api_follow_the_roles(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    app.auth.create_user("inv", "inv-password")
    grant_role(app, "inv", env, "investigator")
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        investigator = Client(base).sign_in("inv", "inv-password")
        ops = Client(base).sign_in("demo", "demo-password")
        bundle = investigated(investigator, env.id)
        inv_id = bundle["investigation"]["id"]
        oom = next(f for f in bundle["findings"] if f["condition"] == "pod.crashloop.oom")
        body = {
            "investigation_id": inv_id,
            "finding_ids": [oom["id"]],
            "kind": "config_change",
            "title": "Raise the limit",
            "change": "memory 256Mi -> 512Mi",
            "intended_effect": "no more OOM kills",
            "verification_condition": "pod.crashloop.oom",
            "verification_capabilities": [
                {
                    "id": "k8s.workload_health",
                    "inputs": {"kind": "Deployment", "namespace": "shop", "name": "api"},
                }
            ],
        }
        status, payload = investigator.call("POST", "/api/proposals", {**body, "intended_effect": ""})
        assert status == 400 and "intended effect" in payload["error"]
        proposal = investigator.ok("POST", "/api/proposals", body)["proposal"]
        assert proposal["state"] == "proposed" and proposal["author"].startswith("user:")
        assert (
            investigator.ok("GET", f"/api/proposals?env={env.id}&investigation={inv_id}")["proposals"][0][
                "id"
            ]
            == proposal["id"]
        )
        status, _ = investigator.call("PATCH", f"/api/proposals/{proposal['id']}", {"state": "approved"})
        assert status == 403  # approval is the operator's
        status, _ = investigator.call("PATCH", f"/api/proposals/{proposal['id']}", {"state": "verified"})
        assert status == 400
        status, _ = ops.call("POST", f"/api/proposals/{proposal['id']}/verify")
        assert status == 409  # not applied yet
        ops.ok("PATCH", f"/api/proposals/{proposal['id']}", {"state": "approved"})
        applied = ops.ok(
            "PATCH", f"/api/proposals/{proposal['id']}", {"state": "applied", "note": "done by hand"}
        )["proposal"]
        assert applied["applied_note"] == "done by hand"
        status, _ = investigator.call("POST", f"/api/proposals/{proposal['id']}/verify")
        assert status == 403
        verified = ops.ok("POST", f"/api/proposals/{proposal['id']}/verify")
        assert (
            verified["verification"]["outcome"] == "not_verified"
            and verified["proposal"]["state"] == "applied"
        )
        app.setup_demo(after_fix=True)
        verified = ops.ok("POST", f"/api/proposals/{proposal['id']}/verify")
        assert (
            verified["verification"]["outcome"] == "verified" and verified["proposal"]["state"] == "verified"
        )
        assert (
            investigator.ok("GET", f"/api/investigations/{inv_id}")["investigation"]["status"] == "completed"
        )
        rejected = investigator.ok("POST", "/api/proposals", body)["proposal"]
        assert (
            investigator.ok(
                "PATCH", f"/api/proposals/{rejected['id']}", {"state": "rejected", "note": "duplicate"}
            )["proposal"]["state"]
            == "rejected"
        )
        actions = [a.action for a in app.store.find(AuditEntry)]
        assert (
            "proposal_created" in actions
            and "proposal_state_changed" in actions
            and "proposal_verified" in actions
        )
    finally:
        server.shutdown()
        server.server_close()


class Opened:
    def __enter__(self) -> Opened:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_saved_investigations_run_compare_and_schedule_on_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, demo_user = app.setup_demo()
    posted: list[dict[str, Any]] = []
    monkeypatch.setenv("K8SHOLMES_TEST_HOOK", "https://hooks.example.test/saved")
    monkeypatch.setattr(
        saved_mod.urllib.request,
        "urlopen",
        lambda request, timeout=0: posted.append(json.loads(request.data)) or Opened(),
    )
    server = make_server(app, port=0, scheduler_interval_s=0.2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base).sign_in("demo", "demo-password")
        status, _ = client.call("POST", "/api/saved", {"environment_id": env.id, "question": " "})
        assert status == 400
        status, payload = client.call(
            "POST",
            "/api/saved",
            {"environment_id": env.id, "question": "q", "notification": {"frequency": "weekly"}},
        )
        assert status == 400 and "invalid input" in payload["error"]  # validated before anything is stored
        body = {
            "environment_id": env.id,
            "question": "Why is api restarting?",
            "namespaces": ["shop"],
            "schedule": {"every_hours": 24},
            "notification": {"frequency": "every_run", "webhook_url_env": "K8SHOLMES_TEST_HOOK"},
        }
        saved = client.ok("POST", "/api/saved", body)["saved"]
        assert saved["schedule"]["next_run_at"] and saved["model_endpoint_id"] == "mdl_demo"
        assert client.ok("GET", f"/api/saved?env={env.id}")["saved"][0]["id"] == saved["id"]
        first = client.ok("POST", f"/api/saved/{saved['id']}/run")["investigation"]
        assert first["saved_id"] == saved["id"]
        client.wait_idle(first["id"])
        second = client.ok("POST", f"/api/saved/{saved['id']}/run")["investigation"]
        client.wait_idle(second["id"])
        deadline = time.monotonic() + 10
        while (
            time.monotonic() < deadline
            and app.store.count(Report, {"kind": "comparison", "subject_id": saved["id"]}) < 2
        ):
            time.sleep(0.05)
        read = client.ok("GET", f"/api/saved/{saved['id']}")
        assert [r["id"] for r in read["runs"]] == [first["id"], second["id"]]
        assert read["comparisons"][1]["summary"].startswith("no meaningful change")
        assert [r["revision"] for r in read["reports"]] == [1, 2]  # one comparison report per finished run
        assert len(posted) == 2 and posted[-1]["summary"] == read["comparisons"][1]["summary"]
        assert posted[-1]["link"].startswith(server.url) and "256Mi" not in json.dumps(posted)
        assert app.store.get(SavedInvestigation, saved["id"]).notification.last_sent_at is not None
        # a follow-up on a run is not a new run: no third comparison, no third notification
        client.ok("POST", f"/api/investigations/{second['id']}/follow_up", {"message": "and the worker?"})
        client.wait_idle(second["id"])
        time.sleep(0.2)
        assert (
            app.store.count(Report, {"kind": "comparison", "subject_id": saved["id"]}) == 2
            and len(posted) == 2
        )
        generated = client.ok(
            "POST", "/api/reports", {"environment_id": env.id, "kind": "comparison", "saved_id": saved["id"]}
        )["report"]
        assert generated["numbers"]["new"] == 0 and generated["revision"] == 3
        status, _ = client.call("PATCH", f"/api/saved/{saved['id']}", {})
        assert status == 400
        # a schedule that is due: the scheduler thread starts the run as the person who saved it
        due = client.ok("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"every_hours": 1}})["saved"]
        stored = app.store.get(SavedInvestigation, due["id"])
        past = stored.schedule.model_copy(update={"next_run_at": datetime.now(UTC) - timedelta(minutes=1)})
        app.store.put(stored.model_copy(update={"schedule": past}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(app.store.get(SavedInvestigation, saved["id"]).runs) < 3:
            time.sleep(0.05)
        runs = app.store.get(SavedInvestigation, saved["id"]).runs
        assert len(runs) == 3
        assert app.store.get(Investigation, runs[-1]).created_by == demo_user.id
        next_run = app.store.get(SavedInvestigation, saved["id"]).schedule.next_run_at
        assert next_run is not None and next_run > datetime.now(UTC)
        client.wait_idle(runs[-1])
        off = client.ok("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"enabled": False}})["saved"]
        assert off["schedule"]["next_run_at"] is None
        assert any(a.action == "saved_investigation_created" for a in app.store.find(AuditEntry))
    finally:
        server.shutdown()
        server.server_close()
        assert not server.scheduler._thread.is_alive()  # type: ignore[union-attr]
