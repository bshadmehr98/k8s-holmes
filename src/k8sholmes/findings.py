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
# claims that need a source the cluster is not (design §7.2); the words that make them
UNSUPPORTED_CLAIMS: dict[str, tuple[str, ...]] = {
    "unused": ("traffic",),
    "isolated from": ("outside",),
    "no alert fired": ("fired",),
    "the trace shows": ("trace",),
}
TRANSITIONS: dict[FindingState, set[FindingState]] = {
    "open": {"acknowledged", "accepted_risk", "fixed"},
    "acknowledged": {"open", "accepted_risk", "fixed"},
    "accepted_risk": {"open"},
    "fixed": {"verified", "open"},
    "verified": {"open"},
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
            parts.append(ev.summary)
            parts.append(json.dumps(ev.data, default=str))
            parts.append(json.dumps(ev.query, default=str))
    return "\n".join(parts)


def _numbers_without_evidence(text: str, evidence_text: str, resources: list[ResourceRef]) -> list[str]:
    names = " ".join(r.name for r in resources)
    missing: list[str] = []
    for number in NUMBER.findall(text):
        plain = number.rstrip("%")
        if plain in evidence_text or number in evidence_text or plain in names:
            continue
        missing.append(number)
    return missing


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
    for k_id in cast(list[Any], payload.get("knowledge_used") or []):
        if str(k_id) in knowledge_ids:
            result.knowledge_used.append(str(k_id))
        else:
            result.problems.append(f"knowledge_used names {k_id!r}, which was not in the context")
    for n, raw in enumerate(cast(list[dict[str, Any]], payload.get("findings") or []), start=1):
        _validate_finding(n, raw, evidence, result)
    return result


def _validate_finding(n: int, raw: dict[str, Any], evidence: dict[str, Evidence], result: Validated) -> None:
    label = f"finding {n} ({str(raw.get('title') or '')[:40]!r})"
    kind = str(raw.get("kind") or "observation")
    if kind not in ("observation", "hypothesis"):
        result.problems.append(
            f"{label}: kind must be observation or hypothesis; only a person marks a conclusion"
        )
        kind = "hypothesis"
    for_ids = [str(x) for x in cast(list[Any], raw.get("evidence_for") or [])]
    against_ids = [str(x) for x in cast(list[Any], raw.get("evidence_against") or [])]
    unknown = [x for x in for_ids + against_ids if x not in evidence]
    if unknown:
        result.problems.append(f"{label}: cites evidence that does not exist: {', '.join(unknown)}")
        return
    if not for_ids:
        result.problems.append(
            f"{label}: has no supporting evidence; cite evidence ids or state it as a limitation"
        )
        return
    condition = str(raw.get("condition") or "").strip().lower()
    if not CONDITION.match(condition):
        slug = re.sub(r"[^a-z0-9]+", "-", condition or str(raw.get("title") or "finding").lower()).strip("-")[
            :40
        ]
        condition = f"custom.{slug or 'finding'}"
    resources = _resources(cast(list[dict[str, Any]], raw.get("resources") or []))
    title, explanation = str(raw.get("title") or "").strip(), str(raw.get("explanation") or "").strip()
    if not title or not explanation:
        result.problems.append(f"{label}: needs a title and an explanation")
        return
    cited = [evidence[x] for x in for_ids]
    if all(ev.coverage in ("no_data", "cannot_answer") for ev in cited):
        result.limitations.append(
            f"{title}: the cited evidence cannot support it "
            f"({', '.join(sorted({ev.coverage for ev in cited}))}); kept as a limitation"
        )
        return
    text = f"{title} {explanation}".lower()
    for claim, needed in UNSUPPORTED_CLAIMS.items():
        if claim in text and not any(
            any(
                word in (ev.coverage_reason + ev.source).lower() and ev.coverage == "observed"
                for word in needed
            )
            for ev in cited
        ):
            result.limitations.append(
                f"{title}: says {claim!r}, which no connected source can establish; kept as a limitation"
            )
            return
    missing_numbers = _numbers_without_evidence(
        f"{title} {explanation}", _evidence_text(for_ids + against_ids, evidence), resources
    )
    if missing_numbers:
        result.problems.append(
            f"{label}: uses numbers that appear in no cited evidence: {', '.join(missing_numbers)}"
        )
        return
    result.findings.append(
        {
            "title": title,
            "explanation": explanation,
            "kind": kind,
            "condition": condition,
            "resources": resources,
            "evidence_for": for_ids,
            "evidence_against": against_ids,
            "uncertainty": str(raw.get("uncertainty") or ""),
            "missing_information": [str(x) for x in cast(list[Any], raw.get("missing_information") or [])],
            "next_steps": [str(x) for x in cast(list[Any], raw.get("next_steps") or [])],
        }
    )


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
            earlier = by_key[key]
            merged_for = sorted(set(earlier.evidence_for) | set(candidate.evidence_for))
            merged_against = sorted(set(earlier.evidence_against) | set(candidate.evidence_against))
            if (merged_for, merged_against, candidate.explanation) != (
                earlier.evidence_for,
                earlier.evidence_against,
                earlier.explanation,
            ):
                revision = Revision(
                    at=now(),
                    author=author,
                    change="revised in a later round",
                    before={"explanation": earlier.explanation, "evidence_for": earlier.evidence_for},
                )
                candidate = earlier.model_copy(
                    update={
                        "explanation": candidate.explanation,
                        "evidence_for": merged_for,
                        "evidence_against": merged_against,
                        "updated_at": now(),
                        "revisions": [*earlier.revisions, revision],
                    }
                )
            else:
                candidate = earlier
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


def set_state(store: Store, finding: Finding, state: FindingState, *, author: str, note: str = "") -> Finding:
    if state not in TRANSITIONS[finding.state]:
        raise InvestigationStateError(f"a finding cannot go from {finding.state} to {state}")
    revision = Revision(
        at=now(),
        author=author,
        change=f"state {finding.state} -> {state}" + (f": {note}" if note else ""),
        before={"state": finding.state},
    )
    updated = finding.model_copy(
        update={"state": state, "updated_at": now(), "revisions": [*finding.revisions, revision]}
    )
    store.put(updated)
    return updated


def revise(store: Store, finding: Finding, *, author: str, change: str, **updates: Any) -> Finding:
    """A user's correction or addition: the earlier text stays in the revision (design §8.4)."""
    allowed = {
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
    bad = set(updates) - allowed
    if bad:
        raise InvestigationStateError(f"these fields are not edited by hand: {', '.join(sorted(bad))}")
    before = {k: getattr(finding, k) for k in updates}
    revision = Revision(
        at=now(),
        author=author,
        change=change,
        before={k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in before.items()},
    )
    updated = finding.model_copy(
        update={**updates, "updated_at": now(), "revisions": [*finding.revisions, revision]}
    )
    store.put(updated)
    return updated
