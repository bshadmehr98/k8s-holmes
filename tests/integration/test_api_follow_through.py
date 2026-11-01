"""The report, proposal and saved-investigation routes (design §10 to §12), the public share route, the live
summary, the model comparison, and the server's scheduler with the comparison after a saved run."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from k8sholmes.app import App, grant_role
from k8sholmes.records import AuditEntry, Report, SavedInvestigation
from k8sholmes.server import make_server, serve_in_thread
from tests.helpers import Client


def investigated(client: Client, env_id: str, question: str = "Why is api restarting?") -> dict[str, Any]:
    inv = client.ok("POST", "/api/investigations", {"environment_id": env_id, "question": question})[
        "investigation"
    ]
    return client.wait_idle(inv["id"])


def test_reports_edits_shares_and_downloads(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    server, _ = serve_in_thread(app)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base).sign_in("demo", "demo-password")
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
        # a share: the token appears once; the public route needs no session; revoked, it is gone
        status, payload = client.call("POST", f"/api/reports/{report['id']}/shares", {"days": 400})
        assert status == 400
        shared = client.ok("POST", f"/api/reports/{report['id']}/shares", {"days": 3})
        assert shared["token"] not in str(shared["share"]) and shared["path"].startswith("/#/shared/")
        anonymous = Client(base)
        public = anonymous.ok("GET", f"/api/shared/{shared['token']}")
        assert public["shared"] is True and public["report"]["id"] == report["id"]
        status, text = anonymous.call("GET", f"/api/shared/{shared['token']}/markdown")
        assert status == 200 and text.startswith("# ")
        status, _ = anonymous.call("GET", f"/api/reports/{report['id']}")
        assert status == 401  # the token opens one report, not the API
        status, _ = anonymous.call("GET", "/api/shared/nope")
        assert status == 404
        assert client.ok("GET", f"/api/reports/{report['id']}")["shares"][0]["id"] == shared["share"]["id"]
        client.ok("POST", f"/api/shares/{shared['share']['id']}/revoke")
        status, _ = anonymous.call("GET", f"/api/shared/{shared['token']}")
        assert status == 404
        actions = {a.action for a in app.store.find(AuditEntry)}
        assert {"report_generated", "report_edited", "share_created", "share_revoked", "export"} <= actions
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
            "POST", f"/api/investigations/{inv_id}/compare_models", {"endpoint_ids": ["mdl_demo"]}
        )
        assert compared["kind"] == "fixed_evidence" and len(compared["reviewed"]) == len(bundle["findings"])
        result = compared["results"][0]
        assert result["model"] and result["findings"] and result["usage"]["prompt_tokens"] >= 0
        assert len(client.ok("GET", f"/api/investigations/{inv_id}")["findings"]) == len(
            bundle["findings"]
        )  # nothing stored
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


def test_saved_investigations_run_compare_and_schedule_on_the_server(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, _ = app.setup_demo()
    server = make_server(app, port=0, scheduler_interval_s=0.2)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = Client(base).sign_in("demo", "demo-password")
        status, _ = client.call("POST", "/api/saved", {"environment_id": env.id, "question": " "})
        assert status == 400
        saved = client.ok(
            "POST",
            "/api/saved",
            {
                "environment_id": env.id,
                "question": "Why is api restarting?",
                "namespaces": ["shop"],
                "schedule": {"every_hours": 24},
                "notification": {"frequency": "every_run"},
            },
        )["saved"]
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
        generated = client.ok(
            "POST", "/api/reports", {"environment_id": env.id, "kind": "comparison", "saved_id": saved["id"]}
        )["report"]
        assert generated["numbers"]["new"] == 0 and generated["revision"] == 3
        status, _ = client.call("PATCH", f"/api/saved/{saved['id']}", {})
        assert status == 400
        # a schedule that is due: the scheduler thread starts the run as the person who saved it
        due = client.ok("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"every_hours": 1}})["saved"]
        stored = app.store.get(SavedInvestigation, due["id"])
        app.store.put(
            stored.model_copy(
                update={
                    "schedule": stored.schedule.model_copy(
                        update={"next_run_at": datetime.now(UTC) - timedelta(minutes=1)}
                    )
                }
            )
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(app.store.get(SavedInvestigation, saved["id"]).runs) < 3:
            time.sleep(0.05)
        runs = app.store.get(SavedInvestigation, saved["id"]).runs
        assert len(runs) == 3
        assert app.store.get(SavedInvestigation, saved["id"]).schedule.next_run_at > datetime.now(UTC)
        client.wait_idle(runs[-1])
        off = client.ok("PATCH", f"/api/saved/{saved['id']}", {"schedule": {"enabled": False}})["saved"]
        assert off["schedule"]["next_run_at"] is None
        assert any(a.action == "saved_investigation_created" for a in app.store.find(AuditEntry))
    finally:
        server.shutdown()
        server.server_close()
