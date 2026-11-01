"""Capabilities over the store: knowledge and exceptions in scope, and evidence of earlier investigations."""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, Field

from k8sholmes import knowledge
from k8sholmes.capabilities import Capability, Context
from k8sholmes.capabilities.kubernetes import Inputs
from k8sholmes.errors import NotFound
from k8sholmes.evidence import Result
from k8sholmes.records import Evidence, Finding, ResourceRef


class ScopeRefInputs(Inputs):
    namespace: str | None = None
    kind: str | None = None
    name: str | None = None
    text: str | None = Field(default=None, description="Words to look for in the notes and explanations.")


def _refs(i: ScopeRefInputs) -> tuple[list[ResourceRef], list[str]]:
    refs = [ResourceRef(kind=i.kind or "", namespace=i.namespace, name=i.name)] if i.name else []
    return refs, [i.namespace] if i.namespace else []


def run_knowledge_search(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ScopeRefInputs, inputs)
    refs, namespaces = _refs(i)
    notes = knowledge.notes_in_scope(ctx.store, ctx.environment.id, refs, namespaces)
    exceptions = knowledge.exceptions_in_scope(ctx.store, ctx.environment.id, refs, namespaces)
    if i.text:
        needle = i.text.lower()
        notes = [n for n in notes if needle in n.text.lower()]
        exceptions = [
            e for e in exceptions if needle in e.explanation.lower() or needle in e.condition.lower()
        ]
    data = {
        "knowledge": [
            {
                "id": n.id,
                "kind": n.kind,
                "scope": n.scope.model_dump() if n.scope else None,
                "text": n.text,
                "author": n.author,
                "source": n.source,
            }
            for n in notes
        ],
        "exceptions": [
            {
                "id": e.id,
                "condition": e.condition,
                "scope": e.scope.model_dump(),
                "applies_to": e.applies_to,
                "explanation": e.explanation,
                "approved_by": e.approved_by,
                "active": knowledge.is_active(e, ctx.now),
                "expires_at": e.expires_at.isoformat() if e.expires_at else None,
            }
            for e in exceptions
        ],
    }
    summary = (
        f"{len(notes)} knowledge notes and {len(exceptions)} exceptions in scope "
        "(user-provided knowledge, not evidence)"
    )
    return Result(
        summary=summary,
        data=data,
        coverage="observed" if notes or exceptions else "observed_absent",
        coverage_reason="what the team has recorded; never a fact about the cluster",
    )


class RecallInputs(Inputs):
    namespace: str | None = None
    name: str | None = None
    condition: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def _evidence_or_none(ctx: Context, evidence_id: str) -> Evidence | None:
    try:
        return ctx.store.get(Evidence, evidence_id)
    except NotFound:  # purged by the retention policy: not recalled
        return None


def run_evidence_recall(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(RecallInputs, inputs)
    findings = ctx.store.find(Finding, {"environment_id": ctx.environment.id}, order_by="created_at DESC")
    chosen: list[Finding] = []
    for f in findings:
        if f.investigation_id == ctx.investigation_id:
            continue
        if i.condition and f.condition != i.condition:
            continue
        if i.name and not any(r.name == i.name and (i.namespace in (None, r.namespace)) for r in f.resources):
            continue
        if i.namespace and not i.name and not any(r.namespace == i.namespace for r in f.resources):
            continue
        chosen.append(f)
        if len(chosen) >= i.limit:
            break
    evidence_ids = {e for f in chosen for e in f.evidence_for}
    evidence: list[dict[str, Any]] = []
    for ev_id in list(evidence_ids)[: i.limit]:
        ev = _evidence_or_none(ctx, ev_id)
        if ev is None:
            continue
        evidence.append(
            {
                "id": ev.id,
                "source": ev.source,
                "collected_at": ev.collected_at.isoformat(),
                "coverage": ev.coverage,
                "summary": ev.summary,
            }
        )
    data = {
        "findings": [
            {
                "id": f.id,
                "investigation_id": f.investigation_id,
                "title": f.title,
                "condition": f.condition,
                "kind": f.kind,
                "state": f.state,
                "resources": [r.model_dump() for r in f.resources],
                "created_at": f.created_at.isoformat(),
                "evidence_for": f.evidence_for,
            }
            for f in chosen
        ],
        "evidence": evidence,
    }
    summary = f"{len(chosen)} earlier findings and {len(evidence)} pieces of evidence in this environment"
    return Result(
        summary=summary,
        data=data,
        coverage="observed" if chosen else "observed_absent",
        coverage_reason="earlier investigations; the cluster may have changed since",
    )


CAPABILITIES: list[Capability] = [
    Capability(
        id="knowledge.search",
        title="Team knowledge and exceptions",
        purpose=(
            "Knowledge notes (ownership, criticality, traffic expectations, maintenance windows, "
            "constraints, dependencies, lessons) and accepted exceptions whose scope touches a resource or "
            "namespace. User-provided, never evidence about the cluster."
        ),
        class_="inspect",
        systems=("k8sholmes",),
        inputs=ScopeRefInputs,
        permissions=(),
        run=run_knowledge_search,
        coverage=("observed", "observed_absent"),
    ),
    Capability(
        id="evidence.recall",
        title="Earlier investigations",
        purpose=(
            "Findings and evidence of earlier investigations in this environment about a resource, "
            "namespace or condition."
        ),
        class_="inspect",
        systems=("k8sholmes",),
        inputs=RecallInputs,
        permissions=(),
        run=run_evidence_recall,
        coverage=("observed", "observed_absent"),
    ),
]
