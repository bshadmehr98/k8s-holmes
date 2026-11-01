"""Retention (design §14.4, §16.3): the purge removes only what the policy says, never anything a reviewed
finding, an exception, a report revision or a saved run references; the audit log says what it removed."""

from __future__ import annotations

from datetime import timedelta

from k8sholmes.auth import Auth
from k8sholmes.records import (
    AcceptedException,
    Call,
    Environment,
    Evidence,
    Finding,
    Investigation,
    Message,
    ModelCall,
    Proposal,
    Report,
    SavedInvestigation,
    Step,
    now,
)
from k8sholmes.reports import cited_evidence
from k8sholmes.store import Store

WITH_INVESTIGATION = (Evidence, Call, Step, Message, Finding, ModelCall)


def reviewed(finding: Finding) -> bool:
    """A finding a person looked at: a conclusion, a state other than open, an owner or a human revision."""
    return (
        finding.kind == "conclusion"
        or finding.state != "open"
        or finding.owner is not None
        or any(r.author.startswith("user:") for r in finding.revisions)
    )


def _kept_investigations(store: Store, environment_id: str) -> set[str]:
    kept = {
        f.investigation_id for f in store.find(Finding, {"environment_id": environment_id}) if reviewed(f)
    }
    for e in store.find(AcceptedException, {"environment_id": environment_id}):
        kept.update(
            f.investigation_id for f in store.get_many(Finding, [e.finding_id] if e.finding_id else [])
        )
    kept.update(r.subject_id for r in store.find(Report, {"environment_id": environment_id}))
    for s in store.find(SavedInvestigation, {"environment_id": environment_id}):
        kept.update(s.runs)
    kept.update(p.investigation_id for p in store.find(Proposal, {"environment_id": environment_id}))
    return kept


def _pinned_evidence(store: Store, environment_id: str) -> set[str]:
    """Evidence cited by a reviewed finding, a verification or a report revision, plus the pinned flag."""
    pinned = {
        e.id for e in store.find(Evidence, {"environment_id": environment_id}) if e.retention == "pinned"
    }
    for f in store.find(Finding, {"environment_id": environment_id}):
        if reviewed(f):
            pinned.update(f.evidence_for)
            pinned.update(f.evidence_against)
    for p in store.find(Proposal, {"environment_id": environment_id}):
        for v in p.verifications:
            pinned.update(v.before_evidence)
            pinned.update(v.after_evidence)
    for r in store.find(Report, {"environment_id": environment_id}):
        pinned.update(cited_evidence(r))
    return pinned


def purge(store: Store, auth: Auth, environment: Environment) -> dict[str, int]:
    """Apply the environment's retention policy once; what was removed is returned and audited."""
    policy = environment.settings.retention
    cutoff_short = now() - timedelta(days=policy.short_evidence_days)
    cutoff_inv = now() - timedelta(days=policy.investigation_days)
    kept = _kept_investigations(store, environment.id)
    pinned = _pinned_evidence(store, environment.id)
    removed = {"evidence": 0, "investigations": 0}
    with store.transaction():
        for ev in store.find(Evidence, {"environment_id": environment.id}):
            if ev.retention == "short" and ev.collected_at < cutoff_short and ev.id not in pinned:
                store.delete(Evidence, ev.id)
                removed["evidence"] += 1
    for inv in store.find(Investigation, {"environment_id": environment.id}):
        stale = inv.updated_at < cutoff_inv and inv.status not in ("running", "queued")
        if not stale or inv.id in kept:
            continue
        with store.transaction():  # one investigation per transaction: small, as §16.3 asks
            for cls in WITH_INVESTIGATION:
                for row in store.find(cls, {"investigation_id": inv.id}):
                    if not (cls is Evidence and row.id in pinned):
                        store.delete(cls, row.id)
            store.delete(Investigation, inv.id)
        removed["investigations"] += 1
    if any(removed.values()):
        auth.audit(None, "retention_purged", environment_id=environment.id, details=removed)
    return removed


__all__ = ["purge", "reviewed"]
