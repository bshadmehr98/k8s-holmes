"""Proposals and their verification (design §11): a concrete improvement backed by findings, never applied
by K8sHolmes; a verification reruns the capabilities and compares before and after by code."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

from k8sholmes import findings as findings_mod
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context, Registry
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import Evidence, Finding, Proposal, ProposalState, Revision, Verification, now
from k8sholmes.store import Store

TRANSITIONS: dict[ProposalState, set[ProposalState]] = {
    "proposed": {"approved", "rejected"},
    "approved": {"applied", "rejected", "proposed"},
    "applied": {"verified", "approved"},
    "verified": {"applied"},
    "rejected": {"proposed"},
}

# Whether a condition still shows in a capability's result: the code checks of a verification (§11.2).
# Each takes the result's data and the resource names the proposal's findings name; None means the result
# says nothing about the condition, and nothing is ever called absent on it.
Detector = Callable[[Any, set[str]], bool | None]


def _oom(data: Any, names: set[str]) -> bool | None:
    if not isinstance(data, dict) or "pods" not in data:
        return None
    pods = cast(list[dict[str, Any]], cast(dict[str, Any], data).get("pods") or [])
    if not pods:
        return None  # no pods were observed: nothing can be called absent
    states = [
        s
        for p in pods
        for s in cast(
            list[dict[str, Any]], cast(dict[str, Any], p.get("summary") or {}).get("container_states") or []
        )
    ]
    return any(
        s.get("last_terminated") == "OOMKilled" or s.get("waiting") == "CrashLoopBackOff" for s in states
    )


def _safeguard(field: str, below: int | None = None) -> Detector:
    def detect(data: Any, names: set[str]) -> bool | None:
        if not isinstance(data, dict):
            return None
        rows = [
            r
            for r in cast(list[dict[str, Any]], cast(dict[str, Any], data).get("workloads") or [])
            if str(r.get("workload", "")).split("/")[-1] in names
        ]
        if not rows:
            return None
        if below is not None:
            return any((r.get(field) or 0) <= below for r in rows)
        return any(not r.get(field) for r in rows)

    return detect


def _no_backends(data: Any, names: set[str]) -> bool | None:
    if not isinstance(data, dict) or "matching_pods" not in data:
        return None
    return not cast(dict[str, Any], data).get("matching_pods")


def _alert_missing(data: Any, names: set[str]) -> bool | None:
    if not isinstance(data, dict) or "workloads_without_alerts" not in data:
        return None
    without = cast(list[str], cast(dict[str, Any], data).get("workloads_without_alerts") or [])
    return bool({str(w).split("/")[-1] for w in without} & names)


DETECTORS: dict[str, Detector] = {
    "pod.crashloop.oom": _oom,
    "replicas.single": _safeguard("replicas", below=1),
    "probe.missing": _safeguard("liveness_probe"),
    "limits.missing": _safeguard("limits"),
    "pdb.missing": _safeguard("pdb"),
    "service.no-backends": _no_backends,
    "alert.missing": _alert_missing,
}


def create_proposal(store: Store, proposal: Proposal) -> Proposal:
    if not proposal.title.strip() or not proposal.change.strip() or not proposal.intended_effect.strip():
        raise InvalidInput("a proposal needs a title, the change itself and its intended effect")
    if not proposal.finding_ids:
        raise InvalidInput("a proposal is backed by at least one finding")
    for finding_id in proposal.finding_ids:
        store.get(Finding, finding_id)
    store.put(proposal)
    return proposal


def set_state(store: Store, proposal: Proposal, state: ProposalState, *, by: str, note: str = "") -> Proposal:
    if state not in TRANSITIONS[proposal.state]:
        raise InvestigationStateError(f"a proposal cannot go from {proposal.state} to {state}")
    if state == "verified":
        raise InvestigationStateError("verified is set by a verification, not by hand")
    change = f"state {proposal.state} -> {state}" + (f": {note}" if note else "")
    revision = Revision(at=now(), author=by, change=change, before={"state": proposal.state})
    updates: dict[str, Any] = {"state": state, "revisions": [*proposal.revisions, revision]}
    if state == "applied":
        updates["applied_note"] = note
    updated = proposal.model_copy(update=updates)
    store.put(updated)
    return updated


def applied_at(proposal: Proposal) -> Any:
    """When the proposal was last marked applied, from its revisions."""
    marks = [r.at for r in proposal.revisions if r.change.startswith("state approved -> applied")]
    return marks[-1] if marks else None


def _rerun(registry: Registry, auth: Auth, ctx: Context, proposal: Proposal) -> list[Evidence]:
    """The proposal's capabilities again, each call committed as it completes (never under one lock)."""
    after: list[Evidence] = []
    for spec in proposal.verification_capabilities:
        call, evidence = registry.execute(
            str(spec.get("id", "")), cast(dict[str, Any], spec.get("inputs") or {}), ctx, auth
        )
        with ctx.store.transaction():
            ctx.store.put(call)
            if evidence is not None:
                ctx.store.put(evidence)
        if evidence is not None:
            after.append(evidence)
    return after


def _judge(condition: str, after: list[Evidence], names: set[str]) -> tuple[str, str]:
    detector = DETECTORS.get(condition)
    if detector is None:
        return (
            "not_verified",
            f"no code check knows {condition!r}; compare the evidence before and after by hand",
        )
    if not after:
        return "not_verified", "no capability ran; nothing to compare"
    seen = [detector(e.data, names) for e in after if e.error is None and e.coverage == "observed"]
    if all(s is None for s in seen):
        return "not_verified", "the capabilities that ran say nothing about the condition"
    if any(seen):
        return "not_verified", f"{condition} still shows in the evidence read after the change"
    return "verified", f"{condition} is observed absent in the evidence read after the change"


def verify(
    registry: Registry, auth: Auth, ctx: Context, proposal: Proposal
) -> tuple[Proposal, list[Evidence]]:
    """Rerun the proposal's capabilities, compare with the findings' evidence, record the outcome (§11.2).

    `verified` only when the condition is observed absent after the change; otherwise `not_verified` with
    what still shows. Unknown conditions are never verified by code: the detail says to compare by hand.
    """
    store, by = ctx.store, f"user:{ctx.user.id}"
    if proposal.state != "applied":
        raise InvestigationStateError(
            f"a proposal is verified after it was applied; this one is {proposal.state}"
        )
    marked = applied_at(proposal)
    if marked is not None and proposal.wait_ms and now() < marked + timedelta(milliseconds=proposal.wait_ms):
        ready = (marked + timedelta(milliseconds=proposal.wait_ms)).isoformat(timespec="seconds")
        raise InvestigationStateError(
            f"the proposal asks to wait {proposal.wait_ms} ms after it was applied; verify from {ready}"
        )
    findings = store.get_many(Finding, proposal.finding_ids)
    names = {r.name for f in findings for r in f.resources}
    ctx = dataclasses.replace(ctx, investigation_id=proposal.investigation_id)
    after = _rerun(registry, auth, ctx, proposal)
    outcome, detail = _judge(proposal.verification_condition, after, names)
    verification = Verification(
        by=by,
        outcome=cast(Any, outcome),
        condition=proposal.verification_condition,
        before_evidence=sorted({e for f in findings for e in f.evidence_for}),
        after_evidence=[e.id for e in after],
        detail=detail,
    )
    updates: dict[str, Any] = {"verifications": [*proposal.verifications, verification]}
    if outcome == "verified":
        revision = Revision(at=now(), author=by, change="verified", before={"state": proposal.state})
        updates.update(state="verified", revisions=[*proposal.revisions, revision])
        for f in findings:
            findings_mod.mark_verified(store, f, author=by, note=f"by proposal {proposal.id}")
    updated = proposal.model_copy(update=updates)
    store.put(updated)
    return updated, after


__all__ = ["DETECTORS", "TRANSITIONS", "applied_at", "create_proposal", "set_state", "verify"]
