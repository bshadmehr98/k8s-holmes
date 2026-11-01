"""Guarantees 8, 9 and 10: exceptions apply exactly and are inspectable; knowledge influence is shown."""

from datetime import timedelta

from k8sholmes import knowledge
from k8sholmes.records import AcceptedException, Finding, ResourceRef, now
from tests.e2e.conftest import Demo

SCHEDULER = ResourceRef(kind="Deployment", namespace="shop", name="scheduler")


def single_replica(demo: Demo, inv) -> dict[str, Finding]:
    return {
        f.resources[0].name: f
        for f in demo.app.store.find(Finding, {"investigation_id": inv.id})
        if f.condition == "replicas.single"
    }


def test_exceptions_apply_exactly_and_expired_or_revoked_ones_are_named(demo: Demo) -> None:
    exc = knowledge.create_exception(
        demo.app.store,
        environment_id=demo.env.id,
        condition="replicas.single",
        scope=SCHEDULER,
        explanation="singleton",
        approved_by=f"user:{demo.user.id}",
    )
    inv = demo.ask("What reliability practices are we overlooking?")
    found = single_replica(demo, inv)
    assert found["scheduler"].state == "accepted_risk"
    assert found["scheduler"].exception_id == exc.id
    assert found["worker"].state == "open"  # another resource, same condition
    assert found["legacy-export"].state == "open"
    probes = [
        f
        for f in demo.app.store.find(Finding, {"investigation_id": inv.id})
        if f.condition == "probe.missing"
    ]
    assert all(f.state == "open" for f in probes)  # another condition is never suppressed
    knowledge.revise_exception(
        demo.app.store,
        exc,
        f"user:{demo.user.id}",
        "expired for the test",
        expires_at=now() - timedelta(days=1),
    )
    again = demo.ask("What reliability practices are we overlooking?")
    scheduler = single_replica(demo, again)["scheduler"]
    assert scheduler.state == "open"
    assert scheduler.exception_note is not None and "expired on" in scheduler.exception_note
    revoked = knowledge.revoke_exception(
        demo.app.store,
        demo.app.store.get(AcceptedException, exc.id),
        f"user:{demo.user.id}",
        "no longer true",
    )
    third = demo.ask("What reliability practices are we overlooking?")
    scheduler = single_replica(demo, third)["scheduler"]
    assert scheduler.state == "open"
    assert "revoked on" in (scheduler.exception_note or "")
    history = demo.app.store.get(AcceptedException, exc.id)
    assert [r.change for r in history.revisions] == ["expired for the test", "revoked: no longer true"]
    assert history.revoked_by == f"user:{demo.user.id}"
    assert revoked.revisions[0].author == f"user:{demo.user.id}"


def test_knowledge_influence_is_shown_and_never_cited_as_evidence(demo: Demo) -> None:
    note = knowledge.create_note(
        demo.app.store,
        environment_id=demo.env.id,
        kind="criticality",
        text="api is tier 1; the checkout team owns it",
        author=f"user:{demo.user.id}",
        scope=ResourceRef(kind="Deployment", namespace="shop", name="api"),
    )
    inv = demo.ask("Why is api restarting?")
    assert note.id in inv.knowledge_in_scope
    findings = demo.app.store.find(Finding, {"investigation_id": inv.id})
    assert findings
    assert all(note.id in f.knowledge_used for f in findings)
    assert all(note.id not in f.evidence_for for f in findings)
    from k8sholmes.cli import report_text

    assert "Influenced by knowledge" in report_text(demo.app, inv)
