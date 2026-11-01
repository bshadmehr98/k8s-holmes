"""The incident review and the reliability and monitoring review (design §10): a timeline from evidence
times, a reconstructed path with its bases, safeguards counted by code with exceptions applied."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes import knowledge
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context, Registry
from k8sholmes.errors import InvalidInput
from k8sholmes.records import (
    AcceptedException,
    Evidence,
    Inventory,
    Investigation,
    Proposal,
    Report,
    ResourceRef,
)
from k8sholmes.reports.base import call_rows, finding_rows, gather, mapping, section, store_report
from k8sholmes.store import Store

BASES = ("defined", "observed", "inferred")
CHECKS = {"liveness_probe": "probe.missing", "limits": "limits.missing", "pdb": "pdb.missing"}


def _timeline(evidence: dict[str, Evidence]) -> list[dict[str, Any]]:
    """Events and changes with a time, from the evidence that holds them, oldest first."""
    rows: list[dict[str, Any]] = []
    for ev in evidence.values():
        data = mapping(ev.data)
        for row in cast(list[dict[str, Any]], data.get("events") or []):
            if row.get("time"):
                what = f"{row.get('reason')}: {row.get('message')}"
                rows.append({"at": row["time"], "what": what, "about": row.get("object"), "evidence": ev.id})
        for row in cast(list[dict[str, Any]], data.get("changes") or []):
            if row.get("time"):
                cause = f" ({row.get('change_cause')})" if row.get("change_cause") else ""
                what = f"{row.get('kind')}: {row.get('resource')}{cause}"
                rows.append(
                    {"at": row["time"], "what": what, "about": row.get("resource"), "evidence": ev.id}
                )
    rows.sort(key=lambda r: str(r["at"]))
    return rows


def _path(store: Store, environment_id: str, named: set[str]) -> list[dict[str, Any]]:
    """The inventory's relationships that touch the named resources, each with its basis (§4.3)."""
    inventories = store.find(Inventory, {"environment_id": environment_id}, order_by="taken_at DESC", limit=1)
    return [
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


def incident_review(store: Store, inv: Investigation, *, by: str) -> Report:
    """An incident review: the timeline from evidence times; the reconstructed path with its bases (§10)."""
    findings, evidence, calls, _ = gather(store, inv)
    timeline = _timeline(evidence)
    path = _path(store, inv.environment_id, {r.name for f in findings for r in f.resources})
    gaps = [e for e in evidence.values() if e.coverage in ("no_data", "cannot_answer")]
    unknown = [*inv.limitations, *[f"{e.source}: {e.coverage_reason}" for e in gaps]]
    proposals = [
        {
            "title": p.title,
            "kind": p.kind,
            "state": p.state,
            "intended_effect": p.intended_effect,
            "author": p.author,
        }
        for p in store.find(Proposal, {"investigation_id": inv.id}, order_by="created_at")
    ]
    numbers = {
        "timeline_entries": len(timeline),
        "path_edges": len(path),
        **{f"path_{b}": sum(1 for p in path if p["basis"] == b) for b in BASES},
        "findings": len(findings),
        "unknowns": len(unknown),
        "proposals": len(proposals),
    }
    sections = [
        section(
            "The incident", "text", {"question": inv.question, "status": inv.status, "answer": inv.answer}
        ),
        section("Timeline", "table", timeline, {"timeline_entries": len(timeline)}),
        section("The reconstructed path (not a trace)", "table", path, {"path_edges": len(path)}),
        section("What was found", "findings", finding_rows(findings, evidence), {"findings": len(findings)}),
        section("What was not known", "list", unknown, {"unknowns": len(unknown)}),
        section("Proposals", "table", proposals, {"proposals": len(proposals)}),
        section("Every call", "table", call_rows(calls), {"calls": len(calls)}),
    ]
    report = Report(
        environment_id=inv.environment_id,
        kind="incident_review",
        title=f"Incident review: {inv.question}",
        subject_id=inv.id,
        generated_by=by,
        sections=sections,
        numbers=numbers,
    )
    return store_report(store, report)


def _workload_ref(workload: str) -> ResourceRef:
    """`Kind/namespace/name` (or `Kind/name`) as the capability writes it."""
    parts = workload.split("/")
    namespace = parts[1] if len(parts) == 3 else None
    return ResourceRef(kind=parts[0], namespace=namespace, name=parts[-1])


def _accepted(row: dict[str, Any], exceptions: list[AcceptedException]) -> list[str]:
    """The conditions this row shows that an active exception covers, matched as the engine does (§9.1)."""
    ref = _workload_ref(str(row["workload"]))
    missing = [cond for field, cond in CHECKS.items() if not row.get(field)]
    if (row.get("replicas") or 0) <= 1:
        missing.append("replicas.single")
    return sorted(
        c for c in missing if any(e.condition == c and knowledge.covers(e, ref) for e in exceptions)
    )


def reliability_review(
    store: Store, registry: Registry, auth: Auth, ctx: Context, namespaces: list[str], *, by: str
) -> Report:
    """Read the safeguards and alert rules through the gate, then count by code with exceptions applied."""
    read: dict[str, Evidence | None] = {}
    for cap_id in ("k8s.safeguards", "k8s.alert_rules"):
        call, evidence = registry.execute(cap_id, {"namespaces": namespaces}, ctx, auth)
        with store.transaction():
            store.put(call)
            if evidence is not None:
                store.put(evidence)
        read[cap_id] = evidence
    safeguards, alert_rules = read["k8s.safeguards"], read["k8s.alert_rules"]
    if safeguards is None:
        raise InvalidInput(
            "k8s.safeguards did not run here; the review needs it (see the capability's history)"
        )
    data = mapping(safeguards.data)
    exceptions = [
        e
        for e in store.find(AcceptedException, {"environment_id": ctx.environment.id})
        if knowledge.is_active(e)
    ]
    rows = [
        {**r, "accepted": _accepted(r, exceptions)}
        for r in cast(list[dict[str, Any]], data.get("workloads") or [])
    ]
    counts = {k: int(v) for k, v in cast(dict[str, Any], data.get("counts") or {}).items()}
    without_alerts = cast(
        list[str], mapping(alert_rules.data if alert_rules else None).get("workloads_without_alerts") or []
    )
    alert_coverage = alert_rules.coverage if alert_rules else "cannot_answer"
    if alert_coverage != "observed":
        reason = alert_rules.coverage_reason if alert_rules else "no alert source was read"
        without_alerts = [f"{alert_coverage}: {reason}"]
    numbers = {
        **counts,
        "accepted_exceptions_applied": sum(len(cast(list[str], r["accepted"])) for r in rows),
        "workloads_without_alerts": len(without_alerts) if alert_coverage == "observed" else 0,
    }
    sources = {
        "environment": ctx.environment.id,
        "safeguards_evidence": safeguards.id,
        "alert_rules_evidence": alert_rules.id if alert_rules else None,
        "alert_coverage": alert_coverage,
        "taken_at": safeguards.collected_at.isoformat(),
    }
    applied = [
        f"{e.condition} on {e.scope.kind} {e.scope.namespace}/{e.scope.name}: {e.explanation}"
        for e in exceptions
    ]
    sections = [
        section("Scope and sources", "text", sources),
        section("Safeguards per workload", "table", rows, {"workloads": len(rows)}),
        section("Counts", "table", [{"check": k, "count": v} for k, v in counts.items()], counts),
        section(
            "Workloads referenced by no alert",
            "list",
            without_alerts,
            {"workloads_without_alerts": numbers["workloads_without_alerts"]},
        ),
        section("Exceptions applied", "list", applied, {"exceptions": len(exceptions)}),
    ]
    report = Report(
        environment_id=ctx.environment.id,
        kind="reliability_review",
        title="Reliability and monitoring review",
        subject_id=f"scope:{','.join(namespaces) or 'all'}",
        generated_by=by,
        sections=sections,
        numbers=numbers,
    )
    return store_report(store, report)
