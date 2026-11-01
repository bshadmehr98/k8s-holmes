"""Saved and recurring investigations (design §12): run by hand or on a schedule, compared with the run
before by finding key, a one-line result when nothing changed, notifications by webhook."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from k8sholmes.errors import InvalidInput
from k8sholmes.records import (
    Comparison,
    Environment,
    Evidence,
    Finding,
    Investigation,
    Report,
    SavedInvestigation,
    Scope,
    User,
    now,
)
from k8sholmes.reports import section, store_report  # the comparison is a report kind of design §10
from k8sholmes.retention import purge
from k8sholmes.store import Store

log = logging.getLogger("k8sholmes")


def create_saved(store: Store, saved: SavedInvestigation) -> SavedInvestigation:
    if not saved.question.strip():
        raise InvalidInput("a saved investigation needs a question")
    if saved.schedule.every_hours is not None and saved.schedule.every_hours < 1:
        raise InvalidInput("a schedule runs at most hourly")
    saved = saved.model_copy(
        update={"schedule": saved.schedule.model_copy(update={"next_run_at": _next_run(saved, now())})}
    )
    store.put(saved)
    return saved


def _next_run(saved: SavedInvestigation, after: datetime) -> datetime | None:
    schedule = saved.schedule
    if not schedule.enabled:
        return None
    if schedule.every_hours:
        return after + timedelta(hours=schedule.every_hours)
    if schedule.daily_at:
        hour, minute = (int(x) for x in schedule.daily_at.split(":"))
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)
    return None


def start_run(
    store: Store,
    engine_start: Callable[..., Investigation],
    saved: SavedInvestigation,
    environment: Environment,
    user: User,
) -> Investigation:
    """A new investigation made from the saved one; the engine runs it (the runner, or the caller)."""
    inv = engine_start(
        environment=environment,
        question=saved.question,
        user=user,
        scope=Scope(namespaces=list(saved.scope.namespaces), resources=list(saved.scope.resources)),
        model_endpoint_id=saved.model_endpoint_id,
        budgets=saved.budgets,
        saved_id=saved.id,
    )
    updated = saved.model_copy(
        update={
            "runs": [*saved.runs, inv.id],
            "schedule": saved.schedule.model_copy(
                update={"last_run_at": now(), "next_run_at": _next_run(saved, now())}
            ),
        }
    )
    store.put(updated)
    return inv


def compare_runs(store: Store, saved: SavedInvestigation, after_id: str) -> Comparison:
    """New, resolved and unchanged findings between a run and the one before it, by finding key (§8.3)."""
    runs = saved.runs
    index = runs.index(after_id) if after_id in runs else len(runs)
    before_id = runs[index - 1] if index >= 1 else None
    after = {f.key(): f for f in store.find(Finding, {"investigation_id": after_id})}
    before = {f.key(): f for f in store.find(Finding, {"investigation_id": before_id})} if before_id else {}
    new = [after[k].id for k in after if k not in before]
    resolved = [before[k].id for k in before if k not in after]
    common = [k for k in after if k in before]
    evidence_changed = [
        after[k].id
        for k in common
        if {_evidence_hash(store, e) for e in after[k].evidence_for}
        != {_evidence_hash(store, e) for e in before[k].evidence_for}
    ]
    unchanged = len(common) - len(evidence_changed)
    if before_id is None:
        summary = f"first run: {len(after)} findings"
    elif not new and not resolved and not evidence_changed:
        summary = f"no meaningful change: {unchanged} findings as before"
    else:
        summary = (
            f"{len(new)} new, {len(resolved)} resolved, {len(evidence_changed)} with changed evidence, "
            f"{unchanged} unchanged"
        )
    return Comparison(
        saved_id=saved.id,
        before_id=before_id,
        after_id=after_id,
        new=new,
        resolved=resolved,
        unchanged=unchanged,
        evidence_changed=evidence_changed,
        summary=summary,
    )


def _evidence_hash(store: Store, evidence_id: str) -> str:
    found = store.get_many(Evidence, [evidence_id])
    return found[0].sha256 if found else evidence_id


def comparison_report(store: Store, saved: SavedInvestigation, comparison: Comparison, *, by: str) -> Report:
    after = {f.id: f for f in store.find(Finding, {"investigation_id": comparison.after_id})}
    before = (
        {f.id: f for f in store.find(Finding, {"investigation_id": comparison.before_id})}
        if comparison.before_id
        else {}
    )

    def rows(ids: list[str], pool: dict[str, Finding]) -> list[dict[str, Any]]:
        return [
            {"title": pool[i].title, "condition": pool[i].condition, "state": pool[i].state}
            for i in ids
            if i in pool
        ]

    numbers = {
        "new": len(comparison.new),
        "resolved": len(comparison.resolved),
        "evidence_changed": len(comparison.evidence_changed),
        "unchanged": comparison.unchanged,
    }
    sections = [
        section(
            "The runs",
            "text",
            {
                "question": saved.question,
                "before": comparison.before_id,
                "after": comparison.after_id,
                "summary": comparison.summary,
            },
        ),
        section("New findings", "table", rows(comparison.new, after), {"new": len(comparison.new)}),
        section(
            "Resolved findings",
            "table",
            rows(comparison.resolved, before),
            {"resolved": len(comparison.resolved)},
        ),
        section(
            "Findings whose evidence changed",
            "table",
            rows(comparison.evidence_changed, after),
            {"evidence_changed": len(comparison.evidence_changed)},
        ),
        section(
            "Unchanged",
            "text",
            {"count": comparison.unchanged, "note": "unchanged findings are counted, not repeated"},
        ),
    ]
    return store_report(
        store,
        kind="comparison",
        environment_id=saved.environment_id,
        subject_id=saved.id,
        title=f"Comparison: {saved.question}",
        sections=sections,
        numbers=numbers,
        by=by,
    )


def notify(saved: SavedInvestigation, comparison: Comparison, link: str) -> bool:
    """Post the summary and a link to the webhook named by the environment variable; never evidence bodies."""
    env_name = saved.notification.webhook_url_env
    url = os.environ.get(env_name or "", "")
    if not url:
        return False
    if (
        saved.notification.frequency == "changes_only"
        and not (comparison.new or comparison.resolved or comparison.evidence_changed)
        and comparison.before_id is not None
    ):
        return False
    if (
        saved.notification.frequency == "daily_digest"
        and saved.notification.last_sent_at
        and saved.notification.last_sent_at > now() - timedelta(hours=23)
    ):
        return False
    payload = json.dumps(
        {
            "text": f"K8sHolmes: {saved.question}\n{comparison.summary}\n{link}",
            "summary": comparison.summary,
            "link": link,
        }
    ).encode()
    if not url.startswith(("https://", "http://")):
        log.warning("notification for %s skipped: the webhook is not an http(s) URL", saved.id)
        return False
    request = urllib.request.Request(  # noqa: S310 - http(s) only, checked above
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310 - http(s) only, checked above
            return True
    except OSError as exc:
        log.warning("notification for %s failed: %s", saved.id, exc)
        return False


class Scheduler:
    """The server's background thread: due saved investigations and the nightly purge (design §12, §16.3)."""

    def __init__(
        self,
        store: Store,
        *,
        run_saved: Callable[[SavedInvestigation], None],
        auth: Any,
        interval_s: float = 60.0,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self.store = store
        self.run_saved = run_saved
        self.auth = auth
        self.interval_s = interval_s
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_purge: datetime | None = None

    def tick(self) -> list[str]:
        """One pass: start what is due; purge once a day. Returns the saved ids started."""
        started: list[str] = []
        at = self.clock()
        for saved in self.store.find(SavedInvestigation):
            due = (
                saved.schedule.enabled
                and saved.schedule.next_run_at is not None
                and saved.schedule.next_run_at <= at
            )
            if due:
                try:
                    self.run_saved(saved)
                    started.append(saved.id)
                except Exception:
                    log.exception("scheduled run of %s failed to start", saved.id)
        if self.last_purge is None or self.last_purge < at - timedelta(hours=24):
            for env in self.store.find(Environment):
                purge(self.store, self.auth, env)
            self.last_purge = at
        return started

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.interval_s):
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler tick failed")

        self._thread = threading.Thread(target=loop, name="k8sholmes-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


__all__ = ["Scheduler", "compare_runs", "comparison_report", "create_saved", "notify", "start_run"]
