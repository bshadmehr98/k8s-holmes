"""The incident review and the reliability and monitoring review (design §10.3, §10.4): a timeline from
evidence times, a reconstructed path with its bases, safeguards counted by code with exceptions applied."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes.records import AcceptedException, Evidence, Inventory, Investigation, Report, now
from k8sholmes.reports.base import finding_rows, gather, mapping, section, store_report
from k8sholmes.store import Store


def incident_review(store: Store, inv: Investigation, *, by: str) -> Report:
    """An incident review: the timeline from evidence times; the reconstructed path with its bases (§10)."""
    findings, evidence, calls, _ = gather(store, inv)
    timeline: list[dict[str, Any]] = []
    for ev in evidence.values():
        data = mapping(ev.data)
        for row in cast(list[dict[str, Any]], data.get("events") or []):
            if row.get("time"):
                timeline.append(
                    {
                        "at": row["time"],
                        "what": f"{row.get('reason')}: {row.get('message')}",
                        "about": row.get("object"),
                        "evidence": ev.id,
                    }
                )
        for row in cast(list[dict[str, Any]], data.get("changes") or []):
            if row.get("time"):
                timeline.append(
                    {
                        "at": row["time"],
                        "what": f"{row.get('kind')}: {row.get('resource')}"
                        + (f" ({row.get('change_cause')})" if row.get("change_cause") else ""),
                        "about": row.get("resource"),
                        "evidence": ev.id,
                    }
                )
    timeline.sort(key=lambda t: str(t["at"]))
    inventories = store.find(
        Inventory, {"environment_id": inv.environment_id}, order_by="taken_at DESC", limit=1
    )
    named = {r.name for f in findings for r in f.resources}
    path = [
        {
            "from": r.source.model_dump(),
            "to": r.target.model_dump(),
            "kind": r.kind,
            "basis": r.basis,
            "detail": r.detail,
        }
        for r in (inventories[0].relationships if inventories else [])
        if r.source.name in named or r.target.name in named
    ]
    unknown = [
        *inv.limitations,
        *[
            f"{e.source}: {e.coverage_reason}"
            for e in evidence.values()
            if e.coverage in ("no_data", "cannot_answer")
        ],
    ]
    numbers = {
        "timeline_entries": len(timeline),
        "path_edges": len(path),
        **{f"path_{b}": sum(1 for p in path if p["basis"] == b) for b in ("defined", "observed", "inferred")},
        "findings": len(findings),
        "unknowns": len(unknown),
    }
    sections = [
        section(
            "The incident", "text", {"question": inv.question, "status": inv.status, "answer": inv.answer}
        ),
        section("Timeline", "table", timeline, {"timeline_entries": len(timeline)}),
        section("The reconstructed path (not a trace)", "table", path, {"path_edges": len(path)}),
        section("What was found", "findings", finding_rows(findings, evidence), {"findings": len(findings)}),
        section("What was not known", "list", unknown, {"unknowns": len(unknown)}),
        section(
            "Every call",
            "table",
            [{"capability": c.capability_id, "status": c.status, "at": c.at.isoformat()} for c in calls],
            {"calls": len(calls)},
        ),
    ]
    return store_report(
        store,
        kind="incident_review",
        environment_id=inv.environment_id,
        subject_id=inv.id,
        title=f"Incident review: {inv.question}",
        sections=sections,
        numbers=numbers,
        by=by,
    )


def reliability_review(
    store: Store, environment_id: str, safeguards: Evidence, alert_rules: Evidence | None, *, by: str
) -> Report:
    """The workloads against their safeguards and alert rules, counted by code, exceptions applied (§10)."""
    data = mapping(safeguards.data)
    rows = cast(list[dict[str, Any]], data.get("workloads") or [])
    counts = cast(dict[str, int], data.get("counts") or {})
    exceptions = [
        e
        for e in store.find(AcceptedException, {"environment_id": environment_id})
        if e.revoked_at is None and (e.expires_at is None or e.expires_at > now())
    ]
    excepted = {(e.condition, e.scope.kind, e.scope.namespace, e.scope.name) for e in exceptions}
    checks = {"liveness_probe": "probe.missing", "limits": "limits.missing", "pdb": "pdb.missing"}
    for row in rows:
        kind, name = str(row["workload"]).split("/")[0], str(row["workload"]).split("/")[-1]
        ns = str(row["workload"]).split("/")[1] if str(row["workload"]).count("/") == 2 else None
        row["accepted"] = sorted(
            cond
            for field, cond in checks.items()
            if not row.get(field) and (cond, kind, ns, name) in excepted
        )
        if (row.get("replicas") or 0) <= 1 and ("replicas.single", kind, ns, name) in excepted:
            row["accepted"].append("replicas.single")
    without_alerts: list[str] = []
    alert_coverage = "cannot_answer"
    if alert_rules is not None:
        without_alerts = cast(list[str], mapping(alert_rules.data).get("workloads_without_alerts") or [])
        alert_coverage = alert_rules.coverage
    numbers = {
        **{k: int(v) for k, v in counts.items()},
        "accepted_exceptions_applied": sum(len(cast(list[str], r.get("accepted", []))) for r in rows),
        "workloads_without_alerts": len(without_alerts),
    }
    sections = [
        section(
            "Scope and sources",
            "text",
            {
                "environment": environment_id,
                "safeguards_evidence": safeguards.id,
                "alert_rules_evidence": alert_rules.id if alert_rules else None,
                "alert_coverage": alert_coverage,
                "taken_at": safeguards.collected_at.isoformat(),
            },
        ),
        section("Safeguards per workload", "table", rows, {"workloads": len(rows)}),
        section(
            "Counts",
            "table",
            [{"check": k, "count": v} for k, v in counts.items()],
            {k: int(v) for k, v in counts.items()},
        ),
        section(
            "Workloads referenced by no alert",
            "list",
            without_alerts
            if alert_coverage == "observed"
            else [
                f"cannot_answer: {alert_rules.coverage_reason if alert_rules else 'no alert source was read'}"
            ],
            {"workloads_without_alerts": len(without_alerts)},
        ),
        section(
            "Exceptions applied",
            "list",
            [
                f"{e.condition} on {e.scope.kind} {e.scope.namespace}/{e.scope.name}: {e.explanation}"
                for e in exceptions
            ],
            {"exceptions": len(exceptions)},
        ),
    ]
    return store_report(
        store,
        kind="reliability_review",
        environment_id=environment_id,
        subject_id=f"scope:{','.join(cast(list[str], data.get('namespaces') or [])) or 'all'}",
        title="Reliability and monitoring review",
        sections=sections,
        numbers=numbers,
        by=by,
    )
