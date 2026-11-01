"""Proposals and their verification (design §11): a concrete improvement backed by findings, never applied
by K8sHolmes; a verification reruns the capabilities and compares before and after by code."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context, Registry
from k8sholmes.cluster import Cluster
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import (
    Environment,
    Evidence,
    Finding,
    Proposal,
    ProposalState,
    Revision,
    User,
    Verification,
    now,
)
from k8sholmes.store import Store

TRANSITIONS: dict[ProposalState, set[ProposalState]] = {
    "proposed": {"approved", "rejected"},
    "approved": {"applied", "rejected", "proposed"},
    "applied": {"verified", "approved"},
    "verified": {"applied"},
    "rejected": {"proposed"},
}

# Whether a condition still shows in a capability's result: the code checks of a verification (§11.2).
# Each takes the result's data and the resource names the proposal's findings name.
Detector = Callable[[Any, set[str]], bool | None]


def _oom(data: Any, names: set[str]) -> bool | None:
    if not isinstance(data, dict):
        return None
    pods = cast(list[dict[str, Any]], cast(dict[str, Any], data).get("pods") or [])
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
    if not isinstance(data, dict):
        return None
    return not cast(dict[str, Any], data).get("matching_pods")


def _alert_missing(data: Any, names: set[str]) -> bool | None:
    if not isinstance(data, dict):
        return None
    without = {
        str(w).split("/")[-1]
        for w in cast(list[str], cast(dict[str, Any], data).get("workloads_without_alerts") or [])
    }
    return bool(without & names)


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
    revision = Revision(
        at=now(),
        author=by,
        change=f"state {proposal.state} -> {state}" + (f": {note}" if note else ""),
        before={"state": proposal.state},
    )
    updates: dict[str, Any] = {"state": state, "revisions": [*proposal.revisions, revision]}
    if state == "applied":
        updates["applied_note"] = note
    updated = proposal.model_copy(update=updates)
    store.put(updated)
    return updated


def _judge(condition: str, after: list[Evidence], names: set[str]) -> tuple[str, str]:
    detector = DETECTORS.get(condition)
    if detector is None:
        return (
            "not_verified",
            f"no code check knows {condition!r}; compare the evidence before and after by hand",
        )
    if not after:
        return "not_verified", "no capability ran; nothing to compare"
    seen = [detector(e.data, names) for e in after if e.error is None]
    if all(s is None for s in seen):
        return "not_verified", "the capabilities that ran say nothing about the condition"
    if any(seen):
        return "not_verified", f"{condition} still shows in the evidence read after the change"
    return "verified", f"{condition} is observed absent in the evidence read after the change"


def verify(
    store: Store,
    registry: Registry,
    auth: Auth,
    *,
    proposal: Proposal,
    environment: Environment,
    cluster: Cluster,
    user: User,
) -> tuple[Proposal, list[Evidence]]:
    """Rerun the proposal's capabilities, compare with the findings' evidence, record the outcome (§11.2).

    `verified` only when the condition is observed absent after the change; otherwise `not_verified` with
    what still shows. Unknown conditions are never verified by code: the detail says to compare by hand.
    """
    if proposal.state != "applied":
        raise InvestigationStateError(
            f"a proposal is verified after it was applied; this one is {proposal.state}"
        )
    findings = store.get_many(Finding, proposal.finding_ids)
    names = {r.name for f in findings for r in f.resources}
    before = sorted({e for f in findings for e in f.evidence_for})
    ctx = Context(
        environment=environment,
        cluster=cluster,
        store=store,
        user=user,
        investigation_id=proposal.investigation_id,
    )
    after: list[Evidence] = []
    with store.transaction():
        for spec in proposal.verification_capabilities:
            call, evidence = registry.execute(
                str(spec.get("id", "")), cast(dict[str, Any], spec.get("inputs") or {}), ctx, auth
            )
            store.put(call)
            if evidence is not None:
                store.put(evidence)
                after.append(evidence)
    outcome, detail = _judge(proposal.verification_condition, after, names)
    verification = Verification(
        by=f"user:{user.id}",
        outcome=cast(Any, outcome),
        condition=proposal.verification_condition,
        before_evidence=before,
        after_evidence=[e.id for e in after],
        detail=detail,
    )
    updates: dict[str, Any] = {"verifications": [*proposal.verifications, verification]}
    if outcome == "verified":
        updates["state"] = "verified"
        updates["revisions"] = [
            *proposal.revisions,
            Revision(at=now(), author=f"user:{user.id}", change="verified", before={"state": proposal.state}),
        ]
        for f in findings:
            if f.state in ("fixed", "open", "acknowledged"):
                store.put(
                    f.model_copy(
                        update={
                            "state": "verified",
                            "updated_at": now(),
                            "revisions": [
                                *f.revisions,
                                Revision(
                                    at=now(),
                                    author=f"user:{user.id}",
                                    change=f"verified by proposal {proposal.id}",
                                    before={"state": f.state},
                                ),
                            ],
                        }
                    )
                )
    updated = proposal.model_copy(update=updates)
    store.put(updated)
    return updated, after


__all__ = ["DETECTORS", "TRANSITIONS", "create_proposal", "set_state", "verify"]
