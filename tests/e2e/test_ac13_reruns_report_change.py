"""Guarantee 13: the second run of a saved investigation lists new, resolved and unchanged findings by key;
with no change its summary is one line. Plus the schedule, the comparison report and the notification."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from k8sholmes import saved as saved_mod
from k8sholmes.errors import InvalidInput
from k8sholmes.records import Finding, Notification, SavedInvestigation, Schedule
from tests.e2e.conftest import Demo


def save(demo: Demo, question: str = "Why is api restarting?", **fields: Any) -> SavedInvestigation:
    return saved_mod.create_saved(
        demo.app.store,
        SavedInvestigation(environment_id=demo.env.id, question=question, created_by=demo.user.id, **fields),
    )


def run(demo: Demo, saved: SavedInvestigation) -> tuple[SavedInvestigation, str]:
    inv = saved_mod.start_run(demo.app.store, demo.app.engine.start, saved, demo.env, demo.user)
    demo.app.engine.run(inv.id, demo.user)
    return demo.app.store.get(SavedInvestigation, saved.id), inv.id


def test_the_second_run_reports_change_by_key_and_no_change_in_one_line(demo: Demo) -> None:
    saved = save(demo)
    saved, first = run(demo, saved)
    saved, second = run(demo, saved)
    assert saved.runs == [first, second]
    assert (
        demo.app.store.get(
            Finding, demo.app.store.find(Finding, {"investigation_id": second})[0].id
        ).investigation_id
        == second
    )
    first_cmp = saved_mod.compare_runs(demo.app.store, saved, first)
    assert first_cmp.before_id is None and first_cmp.summary.startswith("first run")
    cmp = saved_mod.compare_runs(demo.app.store, saved, second)
    assert cmp.before_id == first and cmp.after_id == second
    assert cmp.new == [] and cmp.resolved == [] and cmp.evidence_changed == []
    assert cmp.unchanged == len(demo.app.store.find(Finding, {"investigation_id": second}))
    assert cmp.summary.startswith("no meaningful change") and "\n" not in cmp.summary
    # the environment changes (the fix lands): the third run resolves the OOM finding
    demo.app.setup_demo(after_fix=True)
    saved, third = run(demo, saved)
    cmp = saved_mod.compare_runs(demo.app.store, saved, third)
    resolved = demo.app.store.get_many(Finding, cmp.resolved)
    assert "pod.crashloop.oom" in {f.condition for f in resolved}
    assert all(f.investigation_id == second for f in resolved)
    assert all(f.investigation_id == third for f in demo.app.store.get_many(Finding, cmp.new))
    report = saved_mod.comparison_report(demo.app.store, saved, cmp, by=f"user:{demo.user.id}")
    assert report.kind == "comparison" and report.subject_id == saved.id
    assert report.numbers == {
        "new": len(cmp.new),
        "resolved": len(cmp.resolved),
        "evidence_changed": len(cmp.evidence_changed),
        "unchanged": cmp.unchanged,
    }
    resolved_section = next(s for s in report.sections if s["heading"] == "Resolved findings")
    assert len(resolved_section["body"]) == len(cmp.resolved)


def test_schedules_compute_the_next_run_and_the_scheduler_starts_what_is_due(demo: Demo) -> None:
    with pytest.raises(InvalidInput, match="hourly"):
        save(demo, schedule=Schedule(every_hours=0.5))
    with pytest.raises(InvalidInput, match="question"):
        save(demo, question="   ")
    hourly = save(demo, schedule=Schedule(every_hours=6))
    assert hourly.schedule.next_run_at is not None
    assert (
        timedelta(hours=5, minutes=59) < hourly.schedule.next_run_at - datetime.now(UTC) <= timedelta(hours=6)
    )
    daily = save(demo, schedule=Schedule(daily_at="03:30"))
    assert daily.schedule.next_run_at is not None and daily.schedule.next_run_at.strftime("%H:%M") == "03:30"
    assert daily.schedule.next_run_at > datetime.now(UTC)
    off = save(demo, schedule=Schedule(every_hours=1, enabled=False))
    assert off.schedule.next_run_at is None
    started: list[str] = []
    later = datetime.now(UTC) + timedelta(hours=25)
    scheduler = saved_mod.Scheduler(
        demo.app.store, run_saved=lambda s: started.append(s.id), auth=demo.app.auth, clock=lambda: later
    )
    assert sorted(scheduler.tick()) == sorted([hourly.id, daily.id])  # due; the disabled one is not
    assert sorted(started) == sorted([hourly.id, daily.id])
    assert scheduler.last_purge == later  # the purge ran once with this tick
    assert scheduler.tick() == []  # nothing new is due... until a run moves next_run_at
    demo.app.store.put(hourly)  # as if the run never updated the schedule
    assert scheduler.tick() == [hourly.id]

    def boom(saved: SavedInvestigation) -> None:
        raise RuntimeError("no model")

    scheduler.run_saved = boom
    demo.app.store.put(hourly)
    assert scheduler.tick() == []  # a broken schedule is logged, not raised


def test_a_run_moves_the_schedule_and_the_notification_carries_the_summary_only(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = save(
        demo,
        schedule=Schedule(every_hours=2),
        notification=Notification(webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="every_run"),
    )
    before = saved.schedule.next_run_at
    saved, first = run(demo, saved)
    assert saved.schedule.last_run_at is not None and saved.schedule.next_run_at is not None
    assert saved.schedule.next_run_at > (before or datetime.min.replace(tzinfo=UTC))
    cmp = saved_mod.compare_runs(demo.app.store, saved, first)
    posted: list[dict[str, Any]] = []

    class Opened:
        def __enter__(self) -> Opened:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float = 0) -> Opened:
        posted.append({"url": request.full_url, "body": json.loads(request.data)})
        return Opened()

    monkeypatch.setattr(saved_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("K8SHOLMES_TEST_HOOK", raising=False)
    assert saved_mod.notify(saved, cmp, "http://127.0.0.1:8800/#/reports/x") is False  # no URL configured
    monkeypatch.setenv("K8SHOLMES_TEST_HOOK", "file:///etc/passwd")
    assert saved_mod.notify(saved, cmp, "http://127.0.0.1:8800/#/reports/x") is False  # http(s) only
    monkeypatch.setenv("K8SHOLMES_TEST_HOOK", "https://hooks.example.test/abc")
    assert saved_mod.notify(saved, cmp, "http://127.0.0.1:8800/#/reports/x") is True
    assert posted[0]["url"] == "https://hooks.example.test/abc"
    assert posted[0]["body"]["summary"] == cmp.summary and posted[0]["body"]["link"].endswith("/reports/x")
    assert set(posted[0]["body"]) == {"text", "summary", "link"}  # never evidence bodies
    assert "256Mi" not in json.dumps(posted[0]["body"])
    # changes_only: a run without change sends nothing; the first run always does
    changes_only = saved.model_copy(
        update={"notification": Notification(webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="changes_only")}
    )
    saved2, second = run(demo, changes_only)
    quiet = saved_mod.compare_runs(demo.app.store, saved2, second)
    assert saved_mod.notify(saved2, quiet, "x") is False
    assert saved_mod.notify(saved2, cmp, "x") is True  # the first run (before_id None) is sent
    digest = saved2.model_copy(
        update={
            "notification": Notification(
                webhook_url_env="K8SHOLMES_TEST_HOOK",
                frequency="daily_digest",
                last_sent_at=datetime.now(UTC),
            )
        }
    )
    assert saved_mod.notify(digest, cmp, "x") is False
    assert len(posted) == 2

    def failing(request: Any, timeout: float = 0) -> Opened:
        raise OSError("connection refused")

    monkeypatch.setattr(saved_mod.urllib.request, "urlopen", failing)
    assert saved_mod.notify(saved, cmp, "x") is False
