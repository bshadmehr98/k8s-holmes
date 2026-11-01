"""Guarantee 18: a proposal's verification reruns its capabilities, records before and after, and sets
`verified` only when the condition is observed absent; investigation completion and verification are
separate states. Nothing applies a proposal."""

from __future__ import annotations

import pytest

from k8sholmes import proposals
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import Evidence, Finding, Investigation, Proposal
from tests.e2e.conftest import Demo

HEALTH = [{"id": "k8s.workload_health", "inputs": {"kind": "Deployment", "namespace": "shop", "name": "api"}}]


def propose(
    demo: Demo, inv: Investigation, condition: str = "pod.crashloop.oom", **fields
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


def verify(demo: Demo, proposal: Proposal) -> tuple[Proposal, list[Evidence]]:
    return proposals.verify(
        demo.app.store,
        demo.app.registry,
        demo.app.auth,
        proposal=proposal,
        environment=demo.env,
        cluster=demo.app.cluster_for(demo.env),
        user=demo.user,
    )


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
    proposal = proposals.set_state(demo.app.store, proposal, "approved", by=by)
    proposal = proposals.set_state(
        demo.app.store, proposal, "applied", by=by, note="kubectl set resources … by hand"
    )
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
    assert proposal.state == "applied"
    assert demo.app.store.get(Finding, finding.id).state == "open"
    # the fix lands: the rerun observes the condition absent
    demo.app.setup_demo(after_fix=True)
    proposal, after = verify(demo, proposal)
    v = proposal.verifications[-1]
    assert v.outcome == "verified" and "observed absent" in v.detail
    assert proposal.state == "verified"
    assert len(proposal.verifications) == 2  # both attempts are kept
    assert demo.app.store.get(Finding, finding.id).state == "verified"
    assert demo.app.store.get(Investigation, inv.id).status == "completed"  # a separate state, untouched
    assert demo.app.store.get(Evidence, v.after_evidence[0]).investigation_id == inv.id
    # the before evidence and the after evidence sit side by side with their times
    before = demo.app.store.get_many(Evidence, v.before_evidence)
    assert all(
        b.collected_at < demo.app.store.get(Evidence, v.after_evidence[0]).collected_at for b in before
    )


def test_unknown_conditions_and_silent_capabilities_are_never_verified_by_code(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    by = f"user:{demo.user.id}"
    unknown, _ = propose(demo, inv, condition="custom.something-else")
    unknown = proposals.set_state(
        demo.app.store, proposals.set_state(demo.app.store, unknown, "approved", by=by), "applied", by=by
    )
    demo.app.setup_demo(after_fix=True)
    unknown, _ = verify(demo, unknown)
    assert (
        unknown.verifications[-1].outcome == "not_verified" and "by hand" in unknown.verifications[-1].detail
    )
    assert unknown.state == "applied"
    silent, _ = propose(demo, inv, capabilities=[{"id": "k8s.events", "inputs": {"namespace": "shop"}}])
    silent = proposals.set_state(
        demo.app.store, proposals.set_state(demo.app.store, silent, "approved", by=by), "applied", by=by
    )
    silent, _ = verify(demo, silent)
    assert (
        silent.verifications[-1].outcome == "not_verified"
        and "say nothing" in silent.verifications[-1].detail
    )
    nothing, _ = propose(demo, inv, capabilities=[])
    nothing = proposals.set_state(
        demo.app.store, proposals.set_state(demo.app.store, nothing, "approved", by=by), "applied", by=by
    )
    nothing, _ = verify(demo, nothing)
    assert "nothing to compare" in nothing.verifications[-1].detail
    refused, _ = propose(
        demo, inv, capabilities=[{"id": "http.probe_url", "inputs": {"url": "http://api.shop.svc/"}}]
    )
    refused = proposals.set_state(
        demo.app.store, proposals.set_state(demo.app.store, refused, "approved", by=by), "applied", by=by
    )
    refused, after = verify(demo, refused)
    assert (
        after == [] and refused.verifications[-1].outcome == "not_verified"
    )  # the gate refused; recorded, not run


def test_a_proposal_needs_findings_and_words(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    with pytest.raises(InvalidInput, match="at least one finding"):
        proposals.create_proposal(
            demo.app.store,
            Proposal(
                environment_id=demo.env.id,
                investigation_id=inv.id,
                kind="task",
                title="t",
                change="c",
                intended_effect="e",
                verification_condition="x",
                author="user:x",
            ),
        )
    with pytest.raises(InvalidInput, match="intended effect"):
        proposals.create_proposal(
            demo.app.store,
            Proposal(
                environment_id=demo.env.id,
                investigation_id=inv.id,
                finding_ids=["fnd_x"],
                kind="task",
                title="t",
                change="c",
                intended_effect=" ",
                verification_condition="x",
                author="user:x",
            ),
        )
    for condition, detector in proposals.DETECTORS.items():
        assert detector("not a mapping", {"api"}) is None, condition
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
