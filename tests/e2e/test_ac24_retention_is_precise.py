"""Guarantee 24: the purge removes only evidence and investigations outside the policy and never anything
referenced by a reviewed finding, an exception, a report revision or a saved run; the audit log says what."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from k8sholmes import findings as findings_mod
from k8sholmes import knowledge, reports, retention
from k8sholmes import saved as saved_mod
from k8sholmes.records import (
    AcceptedException,
    AuditEntry,
    Call,
    Evidence,
    Finding,
    Investigation,
    ModelCall,
    SavedInvestigation,
    Step,
    now,
)
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.model import calls, structured, text
from tests.e2e.conftest import Demo

OLD = now() - timedelta(days=400)
FRAME = {
    "question": "logs",
    "namespaces": ["shop"],
    "resources": [],
    "period_hours": 24,
    "hypotheses": [],
    "intended_capabilities": [],
}


def reads_logs(demo: Demo) -> None:
    """A model that reads the api's logs (short-lived evidence) and concludes on the workload's health."""

    def script(messages: list[dict[str, Any]], tools: Any, schema: Any):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            evidence = demo.app.store.find(Evidence)
            health = next(e for e in reversed(evidence) if e.source == "k8s.workload_health")
            finding = {
                "title": "api is OOM-killed",
                "explanation": "The api container was OOMKilled.",
                "kind": "observation",
                "condition": "pod.crashloop.oom",
                "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
                "evidence_for": [health.id],
                "evidence_against": [],
                "uncertainty": "",
                "missing_information": [],
                "next_steps": [],
            }
            return structured(
                {
                    "answer": "OOM",
                    "findings": [finding],
                    "limitations": [],
                    "next_steps": [],
                    "knowledge_used": [],
                }
            )
        if not any(m.get("role") == "tool" for m in messages):
            return calls(
                ("k8s.workload_health", {"kind": "Deployment", "namespace": "shop", "name": "api"}),
                ("k8s.logs", {"pod": "api-7d9f-x1y2z", "namespace": "shop"}),
            )
        return text("done")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=script)


def logs_of(demo: Demo, inv: Investigation) -> Evidence:
    found = [e for e in demo.app.store.find(Evidence, {"investigation_id": inv.id}) if e.source == "k8s.logs"]
    assert len(found) == 1 and found[0].retention == "short"
    return found[0]


def age(demo: Demo, inv: Investigation) -> None:
    """Make an investigation and its evidence older than any policy."""
    demo.app.store.put(
        demo.app.store.get(Investigation, inv.id).model_copy(update={"updated_at": OLD, "created_at": OLD})
    )
    for ev in demo.app.store.find(Evidence, {"investigation_id": inv.id}):
        demo.app.store.put(ev.model_copy(update={"collected_at": OLD}))


def test_the_purge_removes_only_what_the_policy_says_and_audits_it(demo: Demo) -> None:
    store = demo.app.store
    reads_logs(demo)
    forgotten = demo.ask("Why is api restarting?")
    reviewed = demo.ask("Why is api restarting?")
    reported = demo.ask("Why is api restarting?")
    saved = saved_mod.save(
        store,
        SavedInvestigation(
            environment_id=demo.env.id, question="Why is api restarting?", created_by=demo.user.id
        ),
    )
    run = saved_mod.start_run(store, demo.app.engine, saved, demo.env, demo.user)
    demo.app.engine.run(run.id, demo.user)
    fresh = demo.ask("Why is api restarting?")
    # the reviewed one: a person acknowledged a finding that cites the logs, which pins them
    finding = store.find(Finding, {"investigation_id": reviewed.id})[0]
    cited_logs = logs_of(demo, reviewed)
    finding = store.put(
        finding.model_copy(update={"evidence_for": [*finding.evidence_for, cited_logs.id]})
    ) or store.get(Finding, finding.id)
    findings_mod.set_state(store, finding, "acknowledged", author=f"user:{demo.user.id}")
    assert store.get(Evidence, cited_logs.id).retention == "pinned"
    # the reported one: an incident review embeds the evidence it cites, logs included through the finding
    reported_finding = store.find(Finding, {"investigation_id": reported.id})[0]
    report_logs = logs_of(demo, reported)
    store.put(
        reported_finding.model_copy(update={"evidence_for": [*reported_finding.evidence_for, report_logs.id]})
    )
    report = reports.investigation_report(store, reported, by=f"user:{demo.user.id}")
    assert report_logs.id in reports.cited_evidence(report)
    uncited_old = [logs_of(demo, forgotten), logs_of(demo, run)]
    for inv in (forgotten, reviewed, reported, run):
        age(demo, inv)
    assert logs_of(demo, fresh).collected_at > now() - timedelta(days=1)
    removed = retention.purge(store, demo.app.auth, demo.env)
    assert removed == {"evidence": 2, "investigations": 1}
    assert store.find(Investigation, {"id": forgotten.id}) == []
    for cls in (Evidence, Call, Step, Finding, ModelCall):
        assert store.count(cls, {"investigation_id": forgotten.id}) == 0
    for kept in (reviewed, reported, run, fresh):
        assert store.get(Investigation, kept.id).question
    assert store.get(Evidence, cited_logs.id).id  # old, short-lived, cited by a reviewed finding: kept
    assert store.get(Evidence, report_logs.id).id  # old, short-lived, embedded in a report revision: kept
    assert all(store.find(Evidence, {"id": e.id}) == [] for e in uncited_old)  # old and uncited: gone
    assert store.get(Evidence, logs_of(demo, fresh).id).retention == "short"  # within the policy: kept
    entries = store.find(AuditEntry, {"action": "retention_purged"})
    assert len(entries) == 1 and entries[0].details == removed and entries[0].environment_id == demo.env.id
    # a second pass finds nothing and writes nothing
    assert retention.purge(store, demo.app.auth, demo.env) == {"evidence": 0, "investigations": 0}
    assert len(store.find(AuditEntry, {"action": "retention_purged"})) == 1
    assert "PLANTED" not in json.dumps([e.details for e in entries])


def test_a_running_investigation_and_an_exception_keep_their_rows(demo: Demo) -> None:
    store = demo.app.store
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
    age(demo, excepted)
    removed = retention.purge(store, demo.app.auth, demo.env)
    assert removed["investigations"] == 0
    assert (
        store.get(Investigation, excepted.id).id and store.get(Investigation, running.id).status == "running"
    )
    assert retention.reviewed(finding) is False
    assert retention.reviewed(finding.model_copy(update={"owner": "team"})) is True
