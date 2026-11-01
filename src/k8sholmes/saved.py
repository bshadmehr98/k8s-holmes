"""Saved and recurring investigations (design §12): run by hand or on a schedule, compared with the run
before by finding key, a one-line result when nothing changed, notifications by webhook."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta
from typing import Any, cast

from k8sholmes.engine import Engine
from k8sholmes.errors import InvalidInput
from k8sholmes.records import (
    Comparison,
    Environment,
    Evidence,
    Finding,
    Investigation,
    Period,
    Report,
    SavedInvestigation,
    Scope,
    User,
    now,
)
from k8sholmes.reports import section, store_report  # the comparison is a report kind of design §10
from k8sholmes.store import Store

log = logging.getLogger("k8sholmes")
VOLATILE_KEYS = ("inventory_id", "period_start", "period_end", "taken_at")  # bookkeeping, not observation
LAST_HOURS = re.compile(r"the last (\d+(?:\.\d+)?) (hour|day)s?", re.IGNORECASE)


def save(store: Store, saved: SavedInvestigation) -> SavedInvestigation:
    """Validate and store a saved investigation (new or changed), computing its next run."""
    if not saved.question.strip():
        raise InvalidInput("a saved investigation needs a question")
    if saved.schedule.every_hours is not None and saved.schedule.every_hours < 1:
        raise InvalidInput("a schedule runs at most hourly")
    schedule = saved.schedule.model_copy(update={"next_run_at": next_run(saved, now())})
    saved = saved.model_copy(update={"schedule": schedule})
    store.put(saved)
    return saved


def next_run(saved: SavedInvestigation, after: datetime) -> datetime | None:
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


def period_of(rule: str) -> Period:
    """The saved period rule as a window ending now; "the last N hours/days" is parsed, anything else is kept
    as the rule alone for the model to read (design §12)."""
    match = LAST_HOURS.search(rule)
    if not match:
        return Period(rule=rule)
    amount, unit = float(match.group(1)), match.group(2).lower()
    hours = amount * 24 if unit == "day" else amount
    end = now()
    return Period(start=end - timedelta(hours=hours), end=end, rule=rule)


def start_run(
    store: Store, engine: Engine, saved: SavedInvestigation, environment: Environment, user: User
) -> Investigation:
    """A new investigation made from the saved one; the engine runs it (the runner, or the caller)."""
    inv = engine.start(
        environment=environment,
        question=saved.question,
        user=user,
        scope=Scope(namespaces=list(saved.scope.namespaces), resources=list(saved.scope.resources)),
        period=period_of(saved.period_rule),
        model_endpoint_id=saved.model_endpoint_id,
        budgets=saved.budgets,
        saved_id=saved.id,
    )
    schedule = saved.schedule.model_copy(update={"last_run_at": now(), "next_run_at": next_run(saved, now())})
    store.put(saved.model_copy(update={"runs": [*saved.runs, inv.id], "schedule": schedule}))
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
    """A digest of what was observed: the stored data without the keys that differ on every read."""
    found = store.get_many(Evidence, [evidence_id])
    if not found:
        return evidence_id
    data = found[0].data
    if isinstance(data, dict):
        data = {k: v for k, v in cast(dict[str, Any], data).items() if k not in VOLATILE_KEYS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def comparison_report(store: Store, saved: SavedInvestigation, comparison: Comparison, *, by: str) -> Report:
    after = {f.id: f for f in store.find(Finding, {"investigation_id": comparison.after_id})}
    before = (
        {f.id: f for f in store.find(Finding, {"investigation_id": comparison.before_id})}
        if comparison.before_id
        else {}
    )
    runs = {
        i.id: i
        for i in store.get_many(Investigation, [x for x in (comparison.before_id, comparison.after_id) if x])
    }

    def rows(ids: list[str], pool: dict[str, Finding]) -> list[dict[str, Any]]:
        return [
            {"title": pool[i].title, "condition": pool[i].condition, "state": pool[i].state}
            for i in ids
            if i in pool
        ]

    models = [
        {
            "run": run_id,
            "model": runs[run_id].model_name,
            "status": runs[run_id].status,
            "at": runs[run_id].created_at.isoformat(),
        }
        for run_id in (comparison.before_id, comparison.after_id)
        if run_id and run_id in runs
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
        section("The models used", "table", models, {"runs": len(models)}),
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
    report = Report(
        environment_id=saved.environment_id,
        kind="comparison",
        title=f"Comparison: {saved.question}",
        subject_id=saved.id,
        generated_by=by,
        sections=sections,
        numbers=numbers,
    )
    return store_report(store, report)


def notify(saved: SavedInvestigation, comparison: Comparison, link: str) -> bool:
    """Post the summary and a link to the webhook named by the environment variable; never evidence bodies."""
    url = os.environ.get(saved.notification.webhook_url_env or "", "")
    frequency, last_sent = saved.notification.frequency, saved.notification.last_sent_at
    changed = bool(comparison.new or comparison.resolved or comparison.evidence_changed)
    if not url:
        return False
    if frequency == "changes_only" and not changed and comparison.before_id is not None:
        return False
    if frequency == "daily_digest" and last_sent and last_sent > now() - timedelta(hours=23):
        return False
    if not url.startswith(("https://", "http://")):
        log.warning("notification for %s skipped: the webhook is not an http(s) URL", saved.id)
        return False
    payload = json.dumps(
        {
            "text": f"K8sHolmes: {saved.question}\n{comparison.summary}\n{link}",
            "summary": comparison.summary,
            "link": link,
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - http(s) only, checked above
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310 - http(s) only, checked above
            return True
    except OSError as exc:
        log.warning("notification for %s failed: %s", saved.id, exc)
        return False


def finish_run(store: Store, inv: Investigation, link_base: str) -> tuple[Comparison, Report] | None:
    """After a saved investigation's run ends: compare by code, store the report, notify (design §12)."""
    if not inv.saved_id or inv.status not in ("completed", "partial"):
        return None
    saved = store.get(SavedInvestigation, inv.saved_id)
    comparison = compare_runs(store, saved, inv.id)
    report = comparison_report(store, saved, comparison, by="scheduler")
    if notify(saved, comparison, f"{link_base}{report.id}"):
        notification = saved.notification.model_copy(update={"last_sent_at": now()})
        store.put(store.get(SavedInvestigation, saved.id).model_copy(update={"notification": notification}))
    return comparison, report


__all__ = [
    "compare_runs",
    "comparison_report",
    "finish_run",
    "notify",
    "period_of",
    "save",
    "start_run",
]
