"""Guarantee 18: a proposal's verification reruns its capabilities, records before and after, and sets
`verified` only when the condition is observed absent; investigation completion and verification are
separate states. Nothing applies a proposal."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from k8sholmes import proposals
from k8sholmes.capabilities import Context
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import Call, Evidence, Finding, Investigation, Proposal, Revision, now
from tests.e2e.conftest import Demo

HEALTH = [{"id": "k8s.workload_health", "inputs": {"kind": "Deployment", "namespace": "shop", "name": "api"}}]


def propose(
    demo: Demo, inv: Investigation, condition: str = "pod.crashloop.oom", **fields: Any
) -> tuple[Proposal, Finding]:
    finding = next(
        f
        for f in demo.app.store.find(Finding, {"investigation_id": inv.id})
        if f.condition == "pod.crashloop.oom"
    )
    proposal = proposals.create_proposal(
        demo.app.store,
        Proposal(
            environment_id=demo.env.id,
            investigation_id=inv.id,
            finding_ids=[finding.id],
            kind="config_change",
            title="Raise api's memory limit to 512Mi",
            change="resources.limits.memory: 256Mi -> 512Mi on Deployment shop/api",
            intended_effect="the api containers stop being OOM-killed",
            verification_capabilities=fields.pop("capabilities", HEALTH),
            verification_condition=condition,
            author=f"user:{demo.user.id}",
            **fields,
        ),
    )
    return proposal, finding


def applied(demo: Demo, proposal: Proposal, note: str = "") -> Proposal:
    by = f"user:{demo.user.id}"
    approved = proposals.set_state(demo.app.store, proposal, "approved", by=by)
    return proposals.set_state(demo.app.store, approved, "applied", by=by, note=note)


def verify(demo: Demo, proposal: Proposal) -> tuple[Proposal, list[Evidence]]:
    ctx = Context(
        environment=demo.env, cluster=demo.app.cluster_for(demo.env), store=demo.app.store, user=demo.user
    )
    return proposals.verify(demo.app.registry, demo.app.auth, ctx, proposal)


def test_verification_reruns_records_before_and_after_and_verifies_only_when_absent(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    assert inv.status == "completed"
    proposal, finding = propose(demo, inv)
    by = f"user:{demo.user.id}"
    with pytest.raises(InvestigationStateError, match="after it was applied"):
        verify(demo, proposal)
    with pytest.raises(InvestigationStateError, match="cannot go"):
        proposals.set_state(demo.app.store, proposal, "applied", by=by)
    with pytest.raises(InvestigationStateError, match="by a verification"):
        proposals.set_state(
            demo.app.store, proposal.model_copy(update={"state": "applied"}), "verified", by=by
        )
    proposal = applied(demo, proposal, "kubectl set resources … by hand")
    assert [r.change for r in proposal.revisions] == [
        "state proposed -> approved",
        "state approved -> applied: kubectl set resources … by hand",
    ]
    assert proposal.applied_note.startswith("kubectl")
    # nothing changed in the cluster yet: the condition still shows
    proposal, after = verify(demo, proposal)
    v = proposal.verifications[-1]
    assert v.outcome == "not_verified" and "still shows" in v.detail
    assert v.before_evidence == sorted(finding.evidence_for)
    assert v.after_evidence == [e.id for e in after] and len(after) == 1
    assert after[0].source.startswith("k8s.workload_health") and after[0].collected_at > finding.created_at
    assert after[0].investigation_id == inv.id
    assert proposal.state == "applied"
    assert demo.app.store.get(Finding, finding.id).state == "open"
    # the fix lands: the rerun observes the condition absent
    demo.app.setup_demo(after_fix=True)
    proposal, after = verify(demo, proposal)
    v = proposal.verifications[-1]
    assert v.outcome == "verified" and "observed absent" in v.detail
    assert proposal.state == "verified"
    assert len(proposal.verifications) == 2  # both attempts are kept
    verified = demo.app.store.get(Finding, finding.id)
    assert verified.state == "verified" and verified.revisions[-1].author == by
    assert f"by proposal {proposal.id}" in verified.revisions[-1].change
    assert all(demo.app.store.get(Evidence, e).retention == "pinned" for e in verified.evidence_for)
    assert demo.app.store.get(Investigation, inv.id).status == "completed"  # a separate state, untouched
    before = demo.app.store.get_many(Evidence, v.before_evidence)
    assert all(
        b.collected_at < demo.app.store.get(Evidence, v.after_evidence[0]).collected_at for b in before
    )
    # reopening: a verified proposal goes back to applied by hand, never the other way round
    reopened = proposals.set_state(demo.app.store, proposal, "applied", by=by, note="it came back")
    assert reopened.state == "applied"


def test_the_wait_after_applying_is_honoured(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    proposal, _ = propose(demo, inv, wait_ms=600_000)
    proposal = applied(demo, proposal)
    with pytest.raises(InvestigationStateError, match="wait 600000 ms"):
        verify(demo, proposal)
    # once the wait has passed (the applied revision is older than the wait), the verification runs
    aged = [r.model_copy(update={"at": r.at - timedelta(minutes=11)}) for r in proposal.revisions]
    proposal = proposal.model_copy(update={"revisions": aged})
    demo.app.store.put(proposal)
    assert proposals.applied_at(proposal) < now() - timedelta(minutes=10)
    proposal, after = verify(demo, proposal)
    assert after and proposal.verifications[-1].outcome == "not_verified"
    assert (
        proposals.applied_at(
            Proposal.model_construct(revisions=[Revision(at=now(), author="x", change="other")])
        )
        is None
    )


def test_unknown_conditions_silent_results_and_no_pods_are_never_verified_by_code(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    unknown, _ = propose(demo, inv, condition="custom.something-else")
    unknown = applied(demo, unknown)
    demo.app.setup_demo(after_fix=True)
    unknown, _ = verify(demo, unknown)
    assert (
        unknown.verifications[-1].outcome == "not_verified" and "by hand" in unknown.verifications[-1].detail
    )
    assert unknown.state == "applied"
    silent, _ = propose(demo, inv, capabilities=[{"id": "k8s.events", "inputs": {"namespace": "shop"}}])
    silent, _ = verify(demo, applied(demo, silent))
    assert (
        silent.verifications[-1].outcome == "not_verified"
        and "say nothing" in silent.verifications[-1].detail
    )
    nothing, _ = propose(demo, inv, capabilities=[])
    nothing, _ = verify(demo, applied(demo, nothing))
    assert "nothing to compare" in nothing.verifications[-1].detail
    # a capability that failed (a workload that does not exist) says nothing: never "absent"
    gone = [
        {
            "id": "k8s.workload_health",
            "inputs": {"kind": "Deployment", "namespace": "shop", "name": "ghost"},
        }
    ]
    failed, _ = propose(demo, inv, capabilities=gone)
    failed, after = verify(demo, applied(demo, failed))
    assert after and after[0].coverage != "observed"
    assert failed.verifications[-1].outcome == "not_verified"
    assert "say nothing" in failed.verifications[-1].detail  # a failed read never makes anything absent
    # a probe is refused by the gate: the refusal is a recorded call, nothing ran
    refused, _ = propose(
        demo, inv, capabilities=[{"id": "http.probe_url", "inputs": {"url": "http://api.shop.svc/"}}]
    )
    refused, after = verify(demo, applied(demo, refused))
    assert after == [] and refused.verifications[-1].outcome == "not_verified" and refused.state == "applied"
    calls = demo.app.store.find(Call, {"investigation_id": inv.id, "status": "refused"})
    assert any(c.capability_id == "http.probe_url" for c in calls)


def test_a_proposal_needs_findings_and_words(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    base: dict[str, Any] = {
        "environment_id": demo.env.id,
        "investigation_id": inv.id,
        "kind": "task",
        "title": "t",
        "change": "c",
        "verification_condition": "x",
        "author": "user:x",
    }
    with pytest.raises(InvalidInput, match="at least one finding"):
        proposals.create_proposal(demo.app.store, Proposal(**base, intended_effect="e"))
    with pytest.raises(InvalidInput, match="intended effect"):
        proposals.create_proposal(
            demo.app.store, Proposal(**base, finding_ids=["fnd_x"], intended_effect=" ")
        )
    for condition, detector in proposals.DETECTORS.items():
        assert detector("not a mapping", {"api"}) is None, condition
        assert detector({"unrelated": 1}, {"api"}) is None, condition
    assert proposals.DETECTORS["pod.crashloop.oom"]({"pods": []}, {"api"}) is None
    assert proposals.DETECTORS["service.no-backends"]({"matching_pods": []}, set()) is True
    assert (
        proposals.DETECTORS["alert.missing"]({"workloads_without_alerts": ["Deployment/shop/api"]}, {"api"})
        is True
    )
    assert (
        proposals.DETECTORS["alert.missing"](
            {"workloads_without_alerts": ["Deployment/shop/api"]}, {"worker"}
        )
        is False
    )
    assert (
        proposals.DETECTORS["replicas.single"](
            {"workloads": [{"workload": "Deployment/shop/api", "replicas": 1}]}, {"api"}
        )
        is True
    )
    assert (
        proposals.DETECTORS["replicas.single"](
            {"workloads": [{"workload": "Deployment/shop/api", "replicas": 3}]}, {"api"}
        )
        is False
    )
    assert (
        proposals.DETECTORS["probe.missing"](
            {"workloads": [{"workload": "Deployment/shop/api", "liveness_probe": False}]}, {"other"}
        )
        is None
    )
