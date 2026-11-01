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
    Proposal,
    Report,
    SavedInvestigation,
    Step,
    now,
)
from k8sholmes.store import Store


def _referenced_investigations(store: Store, environment_id: str) -> set[str]:
    kept: set[str] = set()
    for f in store.find(Finding, {"environment_id": environment_id}):
        if (
            f.kind == "conclusion"
            or f.state in ("acknowledged", "accepted_risk", "fixed", "verified")
            or f.owner
            or any(r.author.startswith("user:") for r in f.revisions)
        ):
            kept.add(f.investigation_id)
    for e in store.find(AcceptedException, {"environment_id": environment_id}):
        if e.finding_id:
            finding = store.get_many(Finding, [e.finding_id])
            if finding:
                kept.add(finding[0].investigation_id)
    for r in store.find(Report, {"environment_id": environment_id}):
        kept.add(r.subject_id)
    for s in store.find(SavedInvestigation, {"environment_id": environment_id}):
        kept.update(s.runs)
    for p in store.find(Proposal, {"environment_id": environment_id}):
        kept.add(p.investigation_id)
    return kept


def _pinned_evidence(store: Store, environment_id: str) -> set[str]:
    pinned = {
        e.id for e in store.find(Evidence, {"environment_id": environment_id}) if e.retention == "pinned"
    }
    for f in store.find(Finding, {"environment_id": environment_id}):
        if f.kind == "conclusion" or f.state != "open":
            pinned.update(f.evidence_for)
            pinned.update(f.evidence_against)
    for p in store.find(Proposal, {"environment_id": environment_id}):
        for v in p.verifications:
            pinned.update(v.before_evidence)
            pinned.update(v.after_evidence)
    return pinned


def purge(store: Store, auth: Auth, environment: Environment) -> dict[str, int]:
    """Apply the environment's retention policy once; what was removed is returned and audited."""
    policy = environment.settings.retention
    cutoff_short = now() - timedelta(days=policy.short_evidence_days)
    cutoff_inv = now() - timedelta(days=policy.investigation_days)
    kept_investigations = _referenced_investigations(store, environment.id)
    pinned = _pinned_evidence(store, environment.id)
    removed = {"evidence": 0, "investigations": 0}
    with store.transaction():
        for ev in store.find(Evidence, {"environment_id": environment.id}):
            if ev.retention == "short" and ev.collected_at < cutoff_short and ev.id not in pinned:
                # the flag and the references agree by construction; both are checked (§16.3)
                store.delete(Evidence, ev.id)
                removed["evidence"] += 1
        for inv in store.find(Investigation, {"environment_id": environment.id}):
            if (
                inv.updated_at < cutoff_inv
                and inv.id not in kept_investigations
                and inv.status not in ("running", "queued")
            ):
                for cls in (Evidence, Call, Step, Message, Finding):
                    for row in store.find(cls, {"investigation_id": inv.id}):  # type: ignore[arg-type]
                        if not (cls is Evidence and row.id in pinned):  # type: ignore[attr-defined]
                            store.delete(cls, row.id)  # type: ignore[attr-defined]
                store.delete(Investigation, inv.id)
                removed["investigations"] += 1
    if any(removed.values()):
        auth.audit(None, "retention_purged", environment_id=environment.id, details=removed)
    return removed


__all__ = ["purge"]
