"""Guarantee 13: the second run of a saved investigation lists new, resolved and unchanged findings by key;
with no change its summary is one line. Plus the schedule, the comparison report and the notification."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from k8sholmes import saved as saved_mod
from k8sholmes.errors import InvalidInput
from k8sholmes.records import Finding, Investigation, Notification, SavedInvestigation, Schedule
from k8sholmes.scheduler import Scheduler
from tests.e2e.conftest import Demo


def save(demo: Demo, question: str = "Why is api restarting?", **fields: Any) -> SavedInvestigation:
    return saved_mod.save(
        demo.app.store,
        SavedInvestigation(environment_id=demo.env.id, question=question, created_by=demo.user.id, **fields),
    )


def run(demo: Demo, saved: SavedInvestigation) -> tuple[SavedInvestigation, str]:
    inv = saved_mod.start_run(demo.app.store, demo.app.engine, saved, demo.env, demo.user)
    demo.app.engine.run(inv.id, demo.user)
    return demo.app.store.get(SavedInvestigation, saved.id), inv.id


def test_the_second_run_reports_change_by_key_and_no_change_in_one_line(demo: Demo) -> None:
    saved = save(demo, period_rule="the last 2 days")
    saved, first = run(demo, saved)
    saved, second = run(demo, saved)
    assert saved.runs == [first, second]
    run_record = demo.app.store.get(Investigation, second)
    assert run_record.saved_id == saved.id and run_record.status == "completed"
    assert run_record.framing is not None and run_record.framing.period.rule == "the last 2 days"
    window = run_record.framing.period.end - run_record.framing.period.start  # type: ignore[operator]
    assert window == timedelta(days=2)  # the saved period rule fixes the window of every run
    first_cmp = saved_mod.compare_runs(demo.app.store, saved, first)
    assert first_cmp.before_id is None and first_cmp.summary.startswith("first run")
    cmp = saved_mod.compare_runs(demo.app.store, saved, second)
    assert cmp.before_id == first and cmp.after_id == second
    assert cmp.new == [] and cmp.resolved == [] and cmp.evidence_changed == []
    assert cmp.unchanged == len(demo.app.store.find(Finding, {"investigation_id": second})) > 0
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
    models = next(s for s in report.sections if s["heading"] == "The models used")["body"]
    assert [m["run"] for m in models] == [second, third] and all(m["model"] for m in models)


def test_period_rules_become_windows_only_when_they_parse() -> None:
    fixed = saved_mod.period_of("the last 36 hours")
    assert (
        fixed.start is not None and fixed.end is not None and fixed.end - fixed.start == timedelta(hours=36)
    )
    days = saved_mod.period_of("The last 3 days")
    assert days.end - days.start == timedelta(days=3)  # type: ignore[operator]
    free = saved_mod.period_of("since the last deploy")
    assert free.start is None and free.rule == "since the last deploy"


def test_schedules_compute_the_next_run_and_the_scheduler_starts_what_is_due(demo: Demo) -> None:
    with pytest.raises(InvalidInput, match="hourly"):
        save(demo, schedule=Schedule(every_hours=0.5))
    with pytest.raises(InvalidInput, match="question"):
        save(demo, question="   ")
    hourly = save(demo, schedule=Schedule(every_hours=6))
    assert hourly.schedule.next_run_at is not None
    until = hourly.schedule.next_run_at - datetime.now(UTC)
    assert timedelta(hours=5, minutes=59) < until <= timedelta(hours=6)
    daily = save(demo, schedule=Schedule(daily_at="03:30"))
    assert daily.schedule.next_run_at is not None and daily.schedule.next_run_at.strftime("%H:%M") == "03:30"
    assert daily.schedule.next_run_at > datetime.now(UTC)
    off = save(demo, schedule=Schedule(every_hours=1, enabled=False))
    assert off.schedule.next_run_at is None
    started: list[str] = []
    later = datetime.now(UTC) + timedelta(hours=25)
    scheduler = Scheduler(
        demo.app.store, run_saved=lambda s: started.append(s.id), auth=demo.app.auth, clock=lambda: later
    )
    assert sorted(scheduler.tick()) == sorted([hourly.id, daily.id])  # due; the disabled one is not
    assert sorted(started) == sorted([hourly.id, daily.id])
    assert scheduler.last_purge == later  # the purge ran once with this tick
    assert scheduler.tick() == []  # a started schedule was moved forward before the run
    demo.app.store.put(hourly)  # as if the schedule had never moved
    assert scheduler.tick() == [hourly.id]

    def boom(saved: SavedInvestigation) -> None:
        raise RuntimeError("no model")

    scheduler.run_saved = boom
    demo.app.store.put(hourly)
    assert scheduler.tick() == []  # a broken schedule is logged, not raised, and never loops
    assert scheduler.tick() == []


class Opened:
    def __enter__(self) -> Opened:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_a_run_moves_the_schedule_and_the_notification_carries_the_summary_only(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = Notification(webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="every_run")
    saved = save(demo, schedule=Schedule(every_hours=2), notification=hook)
    before = saved.schedule.next_run_at
    saved, first = run(demo, saved)
    assert saved.schedule.last_run_at is not None and saved.schedule.next_run_at is not None
    assert saved.schedule.next_run_at > (before or datetime.min.replace(tzinfo=UTC))
    cmp = saved_mod.compare_runs(demo.app.store, saved, first)
    posted: list[dict[str, Any]] = []

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
    quiet_hook = Notification(webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="changes_only")
    saved2, second = run(demo, saved.model_copy(update={"notification": quiet_hook}))
    quiet = saved_mod.compare_runs(demo.app.store, saved2, second)
    assert saved_mod.notify(saved2, quiet, "x") is False
    assert saved_mod.notify(saved2, cmp, "x") is True  # the first run (before_id None) is sent
    # daily_digest: once a day, whether or not anything changed
    for last_sent, expected in (
        (None, True),
        (datetime.now(UTC) - timedelta(hours=24), True),
        (datetime.now(UTC), False),
    ):
        digest = Notification(
            webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="daily_digest", last_sent_at=last_sent
        )
        assert saved_mod.notify(saved2.model_copy(update={"notification": digest}), quiet, "x") is expected, (
            last_sent
        )
    assert len(posted) == 4
    # finish_run does the whole follow-through and records when the digest was sent
    done = saved_mod.finish_run(
        demo.app.store, demo.app.store.get(Investigation, second), "http://x/#/reports/"
    )
    assert done is not None
    comparison, report = done
    assert comparison.summary == quiet.summary and report.kind == "comparison"
    assert (
        posted[-1]["body"]["link"] == f"http://x/#/reports/{report.id}"
        or posted[-1]["body"]["summary"] == quiet.summary
    )
    assert (
        saved_mod.finish_run(
            demo.app.store,
            demo.app.store.get(Investigation, second).model_copy(update={"saved_id": None}),
            "x",
        )
        is None
    )

    def failing(request: Any, timeout: float = 0) -> Opened:
        raise OSError("connection refused")

    monkeypatch.setattr(saved_mod.urllib.request, "urlopen", failing)
    assert saved_mod.notify(saved, cmp, "x") is False


def test_finish_run_records_the_digest_time(demo: Demo, monkeypatch: pytest.MonkeyPatch) -> None:
    hook = Notification(webhook_url_env="K8SHOLMES_TEST_HOOK", frequency="every_run")
    saved = save(demo, notification=hook)
    saved, first = run(demo, saved)
    monkeypatch.setenv("K8SHOLMES_TEST_HOOK", "https://hooks.example.test/abc")
    monkeypatch.setattr(saved_mod.urllib.request, "urlopen", lambda request, timeout=0: Opened())
    assert saved_mod.finish_run(
        demo.app.store, demo.app.store.get(Investigation, first), "http://x/#/reports/"
    )
    assert demo.app.store.get(SavedInvestigation, saved.id).notification.last_sent_at is not None
