"""Findings: validating a conclusion, storing it with exceptions applied, states and revisions (§6.3, §8)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

from k8sholmes import knowledge
from k8sholmes.errors import InvestigationStateError
from k8sholmes.records import (
    AcceptedException,
    Evidence,
    Finding,
    FindingState,
    Investigation,
    Period,
    ResourceRef,
    Revision,
    now,
)
from k8sholmes.store import Store

CONDITION = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+$")
NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?%?(?![\w.-])")
# claims that need a source the cluster is not (design §7.2): the words that make them, and the word a
# supporting observed source's id or reason must carry
UNSUPPORTED_CLAIMS: dict[str, str] = {
    "unused": "traffic",
    "isolated from": "outside",
    "no alert fired": "fired",
    "the trace shows": "trace",
}
TRANSITIONS: dict[FindingState, set[FindingState]] = {
    "open": {"acknowledged", "accepted_risk", "fixed"},
    "acknowledged": {"open", "accepted_risk", "fixed"},
    "accepted_risk": {"open"},
    "fixed": {"verified", "open"},
    "verified": {"open"},
}
EDITABLE = {
    "title",
    "explanation",
    "kind",
    "uncertainty",
    "missing_information",
    "next_steps",
    "owner",
    "evidence_for",
    "evidence_against",
}


@dataclass
class Validated:
    findings: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    limitations: list[str] = field(default_factory=list[str])
    next_steps: list[str] = field(default_factory=list[str])
    knowledge_used: list[str] = field(default_factory=list[str])
    answer: str = ""
    problems: list[str] = field(default_factory=list[str])  # what the model must repair


def _resources(raw: list[dict[str, Any]]) -> list[ResourceRef]:
    return [
        ResourceRef(kind=str(r.get("kind", "")), namespace=r.get("namespace"), name=str(r.get("name", "")))
        for r in raw
        if r.get("name")
    ]


def _evidence_text(ids: list[str], evidence: dict[str, Evidence]) -> str:
    parts: list[str] = []
    for ev_id in ids:
        ev = evidence.get(ev_id)
        if ev is not None:
            parts += [ev.summary, json.dumps(ev.data, default=str), json.dumps(ev.query, default=str)]
    return "\n".join(parts)


def _numbers_without_evidence(text: str, evidence_text: str, resources: list[ResourceRef]) -> list[str]:
    names = " ".join(r.name for r in resources)
    return [
        n
        for n in NUMBER.findall(text)
        if n.rstrip("%") not in evidence_text and n not in evidence_text and n.rstrip("%") not in names
    ]


def validate_conclusion(
    payload: dict[str, Any], evidence: dict[str, Evidence], knowledge_ids: set[str]
) -> Validated:
    """Check the model's conclusion against the evidence (design §6.3).

    Findings that fail stay out; `problems` lists what the model must repair. Findings whose coverage
    does not support them become limitations. Numbers must appear in cited evidence.
    """
    result = Validated(
        answer=str(payload.get("answer") or ""),
        limitations=[str(x) for x in cast(list[Any], payload.get("limitations") or [])],
        next_steps=[str(x) for x in cast(list[Any], payload.get("next_steps") or [])],
    )
    if not payload:
        result.problems.append("the reply was not the JSON object the schema asks for")
        return result
    for k_id in cast(list[Any], payload.get("knowledge_used") or []):
        if str(k_id) in knowledge_ids:
            result.knowledge_used.append(str(k_id))
        else:
            result.problems.append(f"knowledge_used names {k_id!r}, which was not in the context")
    for n, raw in enumerate(cast(list[dict[str, Any]], payload.get("findings") or []), start=1):
        label = f"finding {n} ({str(raw.get('title') or '')[:40]!r})"
        problem = _refusal_for(label, raw, evidence)
        if problem:
            result.problems.append(problem)
            continue
        data = _finding_data(raw)
        limitation = _limitation_for(data, [evidence[x] for x in data["evidence_for"]])
        if limitation:
            result.limitations.append(limitation)
        else:
            result.findings.append(data)
    return result


def _refusal_for(label: str, raw: dict[str, Any], evidence: dict[str, Evidence]) -> str | None:
    """Why a finding goes back for repair: the wrong kind, unknown or missing evidence, invented numbers."""
    if str(raw.get("kind") or "observation") not in ("observation", "hypothesis"):
        return f"{label}: kind must be observation or hypothesis; only a person marks a conclusion"
    for_ids = [str(x) for x in cast(list[Any], raw.get("evidence_for") or [])]
    against_ids = [str(x) for x in cast(list[Any], raw.get("evidence_against") or [])]
    unknown = [x for x in for_ids + against_ids if x not in evidence]
    if unknown:
        return f"{label}: cites evidence that does not exist: {', '.join(unknown)}"
    if not for_ids:
        return f"{label}: has no supporting evidence; cite evidence ids or state it as a limitation"
    title, explanation = str(raw.get("title") or "").strip(), str(raw.get("explanation") or "").strip()
    if not title or not explanation:
        return f"{label}: needs a title and an explanation"
    resources = _resources(cast(list[dict[str, Any]], raw.get("resources") or []))
    missing = _numbers_without_evidence(
        f"{title} {explanation}", _evidence_text(for_ids + against_ids, evidence), resources
    )
    if missing:
        return f"{label}: uses numbers that appear in no cited evidence: {', '.join(missing)}"
    return None


def _limitation_for(data: dict[str, Any], cited: list[Evidence]) -> str | None:
    """Why a finding becomes a limitation: the coverage of its evidence cannot support the claim (§7.2)."""
    title = str(data["title"])
    if all(ev.coverage in ("no_data", "cannot_answer") for ev in cited):
        kinds = ", ".join(sorted({ev.coverage for ev in cited}))
        return f"{title}: the cited evidence cannot support it ({kinds}); kept as a limitation"
    text = f"{title} {data['explanation']}".lower()
    for claim, needed in UNSUPPORTED_CLAIMS.items():
        supported = any(
            ev.coverage == "observed" and needed in (ev.coverage_reason + ev.source).lower() for ev in cited
        )
        if claim in text and not supported:
            return f"{title}: says {claim!r}, which no connected source can establish; kept as a limitation"
    return None


def _finding_data(raw: dict[str, Any]) -> dict[str, Any]:
    condition = str(raw.get("condition") or "").strip().lower()
    if not CONDITION.match(condition):
        slug = re.sub(r"[^a-z0-9]+", "-", condition or str(raw.get("title") or "finding").lower()).strip("-")[
            :40
        ]
        condition = f"custom.{slug or 'finding'}"
    return {
        "title": str(raw.get("title") or "").strip(),
        "explanation": str(raw.get("explanation") or "").strip(),
        "kind": str(raw.get("kind") or "observation"),
        "condition": condition,
        "resources": _resources(cast(list[dict[str, Any]], raw.get("resources") or [])),
        "evidence_for": [str(x) for x in cast(list[Any], raw.get("evidence_for") or [])],
        "evidence_against": [str(x) for x in cast(list[Any], raw.get("evidence_against") or [])],
        "uncertainty": str(raw.get("uncertainty") or ""),
        "missing_information": [str(x) for x in cast(list[Any], raw.get("missing_information") or [])],
        "next_steps": [str(x) for x in cast(list[Any], raw.get("next_steps") or [])],
    }


def store_findings(
    store: Store,
    investigation: Investigation,
    validated: Validated,
    *,
    author: str,
    exceptions: list[AcceptedException],
    existing: list[Finding] | None = None,
) -> list[Finding]:
    """Persist the validated findings: one per key, exceptions applied, earlier ones revised (§8.3, §8.4)."""
    by_key: dict[str, Finding] = {f.key(): f for f in (existing or [])}
    stored: list[Finding] = []
    period = investigation.framing.period if investigation.framing else Period()
    for data in validated.findings:
        candidate = Finding(
            investigation_id=investigation.id,
            environment_id=investigation.environment_id,
            author=author,
            period=period,
            knowledge_used=list(validated.knowledge_used),
            **data,
        )
        key = candidate.key()
        if key in by_key:
            candidate = _merge(by_key[key], candidate, author)
        else:
            exc, note = knowledge.matching_exception(candidate, exceptions)
            if exc is not None:
                candidate = candidate.model_copy(update={"state": "accepted_risk", "exception_id": exc.id})
            elif note:
                candidate = candidate.model_copy(update={"exception_note": note})
        by_key[key] = candidate
        store.put(candidate)
        stored.append(candidate)
    return stored


def _merge(earlier: Finding, candidate: Finding, author: str) -> Finding:
    """A later round's version of the same finding: merged evidence, the earlier text kept as a revision."""
    merged_for = sorted(set(earlier.evidence_for) | set(candidate.evidence_for))
    merged_against = sorted(set(earlier.evidence_against) | set(candidate.evidence_against))
    if (merged_for, merged_against, candidate.explanation) == (
        earlier.evidence_for,
        earlier.evidence_against,
        earlier.explanation,
    ):
        return earlier
    revision = Revision(
        at=now(),
        author=author,
        change="revised in a later round",
        before={"explanation": earlier.explanation, "evidence_for": earlier.evidence_for},
    )
    return earlier.model_copy(
        update={
            "explanation": candidate.explanation,
            "evidence_for": merged_for,
            "evidence_against": merged_against,
            "updated_at": now(),
            "revisions": [*earlier.revisions, revision],
        }
    )


def set_state(store: Store, finding: Finding, state: FindingState, *, author: str, note: str = "") -> Finding:
    """A person moves a finding along TRANSITIONS (design §8.2)."""
    if state not in TRANSITIONS[finding.state]:
        raise InvestigationStateError(f"a finding cannot go from {finding.state} to {state}")
    return _transition(store, finding, state, author=author, note=note)


def mark_verified(store: Store, finding: Finding, *, author: str, note: str) -> Finding:
    """A verification observed the condition absent: `verified` from any state but accepted risk (§11.2)."""
    if finding.state in ("verified", "accepted_risk"):
        return finding
    return _transition(store, finding, "verified", author=author, note=note)


def _transition(store: Store, finding: Finding, state: FindingState, *, author: str, note: str) -> Finding:
    change = f"state {finding.state} -> {state}" + (f": {note}" if note else "")
    revision = Revision(at=now(), author=author, change=change, before={"state": finding.state})
    updated = finding.model_copy(
        update={"state": state, "updated_at": now(), "revisions": [*finding.revisions, revision]}
    )
    store.put(updated)
    if state != "open":
        pin_evidence(store, updated)
    return updated


def pin_evidence(store: Store, finding: Finding) -> None:
    """A reviewed finding's evidence outlives the retention policy (design §7.1, §16.3)."""
    for ev in store.get_many(Evidence, [*finding.evidence_for, *finding.evidence_against]):
        if ev.retention != "pinned":
            store.put(ev.model_copy(update={"retention": "pinned"}))


def revise(store: Store, finding: Finding, *, author: str, change: str, **updates: Any) -> Finding:
    """A user's correction or addition: the earlier text stays in the revision (design §8.4)."""
    bad = set(updates) - EDITABLE
    if bad:
        raise InvestigationStateError(f"these fields are not edited by hand: {', '.join(sorted(bad))}")
    before = {k: _plain(getattr(finding, k)) for k in updates}
    revision = Revision(at=now(), author=author, change=change, before=before)
    updated = finding.model_copy(
        update={**updates, "updated_at": now(), "revisions": [*finding.revisions, revision]}
    )
    store.put(updated)
    return updated


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [_plain(v) for v in cast(list[Any], value)]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
