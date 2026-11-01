"""Guarantees 11 and 12: every number in a report is counted from records and a model's text cannot change
it; a person's edit to a finding or a report is a revision with a human byline, apart from generated text."""

from __future__ import annotations

from datetime import timedelta

import pytest

from k8sholmes import findings as findings_mod
from k8sholmes import knowledge, proposals, reports
from k8sholmes.capabilities import Context
from k8sholmes.errors import InvalidInput, NotFound
from k8sholmes.records import (
    AcceptedException,
    Call,
    Evidence,
    Finding,
    ModelCall,
    Proposal,
    Report,
    ResourceRef,
    now,
)
from tests.e2e.conftest import Demo

SCHEDULER = ResourceRef(kind="Deployment", namespace="shop", name="scheduler")


def counted_bodies_match(report: Report) -> None:
    for section in report.sections:
        for name, n in section["counted"].items():
            if isinstance(section["body"], list) and name != "limitations":
                assert n == len(section["body"]), (section["heading"], name)


def test_every_number_is_counted_from_records_and_a_model_cannot_change_it(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    store = demo.app.store
    report = reports.investigation_report(store, inv, by=f"user:{demo.user.id}")
    assert report.revision == 1
    assert report.numbers["findings"] == store.count(Finding, {"investigation_id": inv.id})
    assert report.numbers["evidence"] == store.count(Evidence, {"investigation_id": inv.id})
    assert report.numbers["calls"] == store.count(Call, {"investigation_id": inv.id})
    assert report.numbers["model_calls"] == store.count(ModelCall, {"investigation_id": inv.id})
    assert report.numbers["prompt_tokens"] == sum(
        m.prompt_tokens for m in store.find(ModelCall, {"investigation_id": inv.id})
    )
    counted_bodies_match(report)
    # a model's prose claiming another count changes nothing: the count is the records
    finding = store.find(Finding, {"investigation_id": inv.id})[0]
    store.put(finding.model_copy(update={"explanation": "There are 99 findings and 400 pieces of evidence."}))
    again = reports.investigation_report(store, inv, by=f"user:{demo.user.id}")
    assert again.revision == 2
    assert again.numbers == report.numbers
    text = reports.to_markdown(again)
    assert "Every number below was counted from stored records" in text
    assert "There are 99 findings" in text  # the prose is quoted, the numbers are not taken from it
    assert f"- findings: {report.numbers['findings']}" in text
    assert reports.revisions(store, again) == [store.get(Report, report.id), again]


def test_the_incident_review_builds_its_timeline_from_evidence_and_names_the_bases(demo: Demo) -> None:
    inv = demo.ask("What changed before this problem started?")
    store = demo.app.store
    finding = store.find(Finding, {"investigation_id": inv.id})[0]
    proposals.create_proposal(
        store,
        Proposal(
            environment_id=demo.env.id,
            investigation_id=inv.id,
            finding_ids=[finding.id],
            kind="task",
            title="Review the rollout",
            change="talk to the team",
            intended_effect="no surprise rollouts",
            verification_condition="change.before-incident",
            author=f"user:{demo.user.id}",
        ),
    )
    report = reports.incident_review(store, inv, by=f"user:{demo.user.id}")
    timeline = next(s for s in report.sections if s["heading"] == "Timeline")
    assert timeline["body"], "the demo's events and changes give the timeline its entries"
    assert report.numbers["timeline_entries"] == len(timeline["body"])
    evidence_ids = {e.id for e in store.find(Evidence, {"investigation_id": inv.id})}
    assert all(entry["evidence"] in evidence_ids for entry in timeline["body"])
    assert [e["at"] for e in timeline["body"]] == sorted(e["at"] for e in timeline["body"])
    path = next(s for s in report.sections if "path" in s["heading"])
    assert all(edge["basis"] in ("defined", "observed", "inferred") for edge in path["body"])
    assert report.numbers["path_edges"] == len(path["body"])
    bases = report.numbers["path_defined"] + report.numbers["path_observed"] + report.numbers["path_inferred"]
    assert bases == len(path["body"])
    proposed = next(s for s in report.sections if s["heading"] == "Proposals")
    assert report.numbers["proposals"] == len(proposed["body"]) == 1
    assert proposed["body"][0]["title"] == "Review the rollout"
    assert reports.cited_evidence(report) >= set(finding.evidence_for) | {
        e["evidence"] for e in timeline["body"]
    }


def review(demo: Demo, namespaces: list[str]) -> Report:
    ctx = Context(
        environment=demo.env, cluster=demo.app.cluster_for(demo.env), store=demo.app.store, user=demo.user
    )
    return reports.reliability_review(
        demo.app.store, demo.app.registry, demo.app.auth, ctx, namespaces, by=f"user:{demo.user.id}"
    )


def test_the_reliability_review_counts_by_code_and_applies_exceptions(demo: Demo) -> None:
    store = demo.app.store
    report = review(demo, ["shop"])
    rows = next(s for s in report.sections if s["heading"] == "Safeguards per workload")["body"]
    assert report.numbers["workloads"] == len(rows) > 0
    assert report.numbers["single_replica"] == sum(
        1 for r in rows if r["kind"] != "DaemonSet" and (r["replicas"] or 0) <= 1
    )
    assert all(r["accepted"] == [] for r in rows)
    sources = next(s for s in report.sections if s["heading"] == "Scope and sources")["body"]
    safeguards = store.get(Evidence, sources["safeguards_evidence"])
    assert safeguards.source.startswith("k8s.safeguards")
    assert all("accepted" not in r for r in safeguards.data["workloads"])  # evidence is never edited
    assert sources["alert_coverage"] == "observed" and report.numbers["workloads_without_alerts"] > 0
    knowledge.create_exception(
        store,
        AcceptedException(
            environment_id=demo.env.id,
            condition="replicas.single",
            scope=SCHEDULER,
            explanation="a singleton by design",
            approved_by=f"user:{demo.user.id}",
        ),
    )
    knowledge.create_exception(
        store,
        AcceptedException(
            environment_id=demo.env.id,
            condition="pdb.missing",
            scope=ResourceRef(kind="Deployment", namespace="shop", name=""),
            applies_to="namespace",
            explanation="no budgets in the shop namespace, by agreement",
            approved_by=f"user:{demo.user.id}",
        ),
    )
    second = review(demo, ["shop"])
    assert second.revision == 2
    rows = next(s for s in second.sections if s["heading"] == "Safeguards per workload")["body"]
    scheduler = next(r for r in rows if r["workload"].endswith("/scheduler"))
    assert "replicas.single" in scheduler["accepted"]
    worker = next(r for r in rows if r["workload"].endswith("/worker"))
    assert "replicas.single" not in worker["accepted"]  # the exception matches one resource exactly
    deployments_without_pdb = [r for r in rows if r["kind"] == "Deployment" and not r["pdb"]]
    assert deployments_without_pdb and all("pdb.missing" in r["accepted"] for r in deployments_without_pdb)
    assert second.numbers["accepted_exceptions_applied"] == sum(len(r["accepted"]) for r in rows)
    applied = next(s for s in second.sections if s["heading"] == "Exceptions applied")
    assert len(applied["body"]) == 2 and any("scheduler" in line for line in applied["body"])
    # the calls went through the gate and are recorded against the environment
    assert store.count(Call, {"capability_id": "k8s.safeguards"}) >= 2


def test_edits_are_revisions_with_a_human_byline_apart_from_generated_text(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    store = demo.app.store
    author = f"user:{demo.user.id}"
    report = reports.investigation_report(store, inv, by=author)
    generated = next(s for s in report.sections if s["heading"] == "The answer")["body"]["answer"]
    edited = reports.add_edit(
        store, report, author=author, section="The answer", text="Confirmed with the team on call."
    )
    assert edited.edits[0].author == author and edited.edits[0].section == "The answer"
    assert next(s for s in edited.sections if s["heading"] == "The answer")["body"]["answer"] == generated
    text = reports.to_markdown(edited)
    assert f"> Edit by {author}" in text
    assert (
        text.index("## The answer")
        < text.index(generated[:30])
        < text.index("> Edit by")
        < text.index("## Findings")
    )
    with pytest.raises(InvalidInput, match="no section"):
        reports.add_edit(store, edited, author=author, section="No such section", text="x")
    with pytest.raises(InvalidInput, match="needs text"):
        reports.add_edit(store, edited, author=author, section="The answer", text="  ")
    # a regeneration is a new revision; the edits stay with the revision they were made on
    second = reports.investigation_report(store, inv, by=author)
    assert second.revision == 2 and second.edits == []
    assert store.get(Report, edited.id).edits[0].text == "Confirmed with the team on call."
    # the same for a finding: the model's text stays readable in the revision
    finding = store.find(Finding, {"investigation_id": inv.id})[0]
    revised = findings_mod.revise(
        store, finding, author=author, change="clarified the title", title="api is OOM-killed"
    )
    assert revised.revisions[-1].author == author
    assert revised.revisions[-1].before["title"] == finding.title
    assert revised.author.startswith("model:")


def test_a_share_link_is_view_only_expiring_and_revocable(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    store = demo.app.store
    report = reports.investigation_report(store, inv, by=f"user:{demo.user.id}")
    share, token = reports.create_share(store, report, by=f"user:{demo.user.id}", days=1)
    assert token not in share.model_dump_json()  # only its hash is stored
    assert reports.shared_report(store, token).id == report.id
    store.put(share.model_copy(update={"expires_at": now() - timedelta(seconds=1)}))
    with pytest.raises(NotFound):
        reports.shared_report(store, token)  # expired
    store.put(share)
    reports.revoke_share(store, share)
    with pytest.raises(NotFound):
        reports.shared_report(store, token)  # revoked
    with pytest.raises(NotFound):
        reports.shared_report(store, "not-a-token")
