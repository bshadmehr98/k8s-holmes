"""Guarantee 24: the purge removes only evidence and investigations outside the policy and never anything
referenced by a reviewed finding, an exception, a report revision or a saved run; the audit log says what."""

from __future__ import annotations

from datetime import timedelta

from k8sholmes import findings as findings_mod
from k8sholmes import reports, retention
from k8sholmes import saved as saved_mod
from k8sholmes.capabilities import Context
from k8sholmes.records import (
    AuditEntry,
    Call,
    Evidence,
    Finding,
    Investigation,
    SavedInvestigation,
    Step,
    now,
)
from tests.e2e.conftest import Demo

OLD = now() - timedelta(days=400)


def read_logs(demo: Demo, inv: Investigation) -> Evidence:
    """Container logs through the gate: the capability marks its evidence short-lived (design §7.1)."""
    ctx = Context(
        environment=demo.env,
        cluster=demo.app.cluster_for(demo.env),
        store=demo.app.store,
        user=demo.user,
        investigation_id=inv.id,
    )
    call, evidence = demo.app.registry.execute(
        "k8s.logs", {"pod": "api-7d9f-x1y2z", "namespace": "shop"}, ctx, demo.app.auth
    )
    assert evidence is not None and evidence.retention == "short", call.reason
    demo.app.store.put(call)
    demo.app.store.put(evidence)
    return evidence


def age(demo: Demo, inv: Investigation) -> None:
    """Make an investigation and its evidence older than any policy."""
    demo.app.store.put(inv.model_copy(update={"updated_at": OLD, "created_at": OLD}))
    for ev in demo.app.store.find(Evidence, {"investigation_id": inv.id}):
        demo.app.store.put(ev.model_copy(update={"collected_at": OLD}))


def test_the_purge_removes_only_what_the_policy_says_and_audits_it(demo: Demo) -> None:
    store = demo.app.store
    forgotten = demo.ask("Why is api restarting?")
    reviewed = demo.ask("Why is api restarting?")
    reported = demo.ask("What changed before this problem started?")
    saved = saved_mod.create_saved(
        store,
        SavedInvestigation(
            environment_id=demo.env.id, question="Why is api restarting?", created_by=demo.user.id
        ),
    )
    run = saved_mod.start_run(store, demo.app.engine.start, saved, demo.env, demo.user)
    demo.app.engine.run(run.id, demo.user)
    fresh = demo.ask("Which important alerts are missing?")
    logs_fresh = read_logs(demo, fresh)
    logs_old = read_logs(demo, reviewed)
    read_logs(demo, forgotten)
    # the reviewed one: a person acknowledged a finding, which pins its evidence
    finding = store.find(Finding, {"investigation_id": reviewed.id})[0]
    findings_mod.set_state(store, finding, "acknowledged", author=f"user:{demo.user.id}")
    assert all(store.get(Evidence, e).retention == "pinned" for e in finding.evidence_for)
    reports.investigation_report(store, reported, by=f"user:{demo.user.id}")
    for inv in (forgotten, reviewed, reported, run):
        age(demo, store.get(Investigation, inv.id))
    assert logs_old.id not in finding.evidence_for  # unpinned, short-lived, and old once aged
    removed = retention.purge(store, demo.app.auth, demo.env)
    assert removed["investigations"] == 1
    assert store.find(Investigation, {"id": forgotten.id}) == []
    for cls in (Evidence, Call, Step, Finding):
        assert store.count(cls, {"investigation_id": forgotten.id}) == 0
    for kept in (reviewed, reported, run, fresh):
        assert store.get(Investigation, kept.id).question
    assert all(store.get(Evidence, e).id for e in finding.evidence_for)  # pinned evidence survives
    assert (
        store.find(Evidence, {"id": logs_old.id}) == []
    )  # old short-lived evidence goes, its investigation stays
    assert store.get(Evidence, logs_fresh.id).retention == "short"  # within the policy: kept
    assert removed["evidence"] >= 1
    entries = store.find(AuditEntry, {"action": "retention_purged"})
    assert len(entries) == 1 and entries[0].details == removed and entries[0].environment_id == demo.env.id
    # a second pass finds nothing and writes nothing
    assert retention.purge(store, demo.app.auth, demo.env) == {"evidence": 0, "investigations": 0}
    assert len(store.find(AuditEntry, {"action": "retention_purged"})) == 1


def test_a_running_investigation_and_an_exception_keep_their_rows(demo: Demo) -> None:
    store = demo.app.store
    from k8sholmes import knowledge
    from k8sholmes.records import AcceptedException

    excepted = demo.ask("What reliability practices are we overlooking?")
    finding = next(
        f for f in store.find(Finding, {"investigation_id": excepted.id}) if f.condition == "replicas.single"
    )
    knowledge.create_exception(
        store,
        AcceptedException(
            environment_id=demo.env.id,
            condition=finding.condition,
            scope=finding.resources[0],
            explanation="x",
            approved_by="user:x",
            finding_id=finding.id,
        ),
    )
    running = demo.app.engine.start(environment=demo.env, question="still going", user=demo.user)
    store.put(running.model_copy(update={"status": "running", "updated_at": OLD}))
    age(demo, store.get(Investigation, excepted.id))
    removed = retention.purge(store, demo.app.auth, demo.env)
    assert removed["investigations"] == 0
    assert (
        store.get(Investigation, excepted.id).id and store.get(Investigation, running.id).status == "running"
    )
