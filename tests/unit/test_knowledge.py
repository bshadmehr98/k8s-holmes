from datetime import timedelta
from pathlib import Path

import pytest

from k8sholmes import knowledge, records
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.store import Store

ENV = "env_1"
SCHED = records.ResourceRef(kind="Deployment", namespace="shop", name="scheduler")
API = records.ResourceRef(kind="Deployment", namespace="shop", name="api")


def finding(condition: str, *refs: records.ResourceRef) -> records.Finding:
    return records.Finding(
        investigation_id="inv",
        environment_id=ENV,
        title="t",
        explanation="e",
        condition=condition,
        resources=list(refs),
        author="model:m",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db")


def test_exception_matches_exactly(store: Store) -> None:
    exc = knowledge.create_exception(
        store,
        environment_id=ENV,
        condition="replicas.single",
        scope=SCHED,
        explanation="singleton scheduler",
        approved_by="user:u",
    )
    active = knowledge.exceptions_in_scope(store, ENV, [SCHED], [])
    assert knowledge.matching_exception(finding("replicas.single", SCHED), active) == (exc, None)
    assert knowledge.matching_exception(finding("replicas.single", API), active) == (
        None,
        None,
    )  # another resource
    assert knowledge.matching_exception(finding("limits.missing", SCHED), active) == (
        None,
        None,
    )  # another condition
    assert knowledge.matching_exception(finding("replicas.single", SCHED, API), active) == (
        None,
        None,
    )  # not every resource
    similar = records.ResourceRef(kind="Deployment", namespace="shop", name="scheduler-2")
    assert knowledge.matching_exception(finding("replicas.single", similar), active) == (None, None)
    other_env = finding("replicas.single", SCHED).model_copy(update={"environment_id": "env_2"})
    assert knowledge.matching_exception(other_env, active) == (None, None)


def test_namespace_wide_exception_needs_explicit_choice(store: Store) -> None:
    with pytest.raises(InvalidInput, match="needs its name"):
        knowledge.create_exception(
            store,
            environment_id=ENV,
            condition="c",
            scope=records.ResourceRef(kind="Deployment", namespace="shop", name=""),
            explanation="x",
            approved_by="u",
        )
    exc = knowledge.create_exception(
        store,
        environment_id=ENV,
        condition="limits.missing",
        scope=records.ResourceRef(kind="Deployment", namespace="dev", name=""),
        explanation="dev has no limits on purpose",
        approved_by="u",
        applies_to="namespace",
    )
    dev = records.ResourceRef(kind="Deployment", namespace="dev", name="anything")
    assert knowledge.matching_exception(finding("limits.missing", dev), [exc])[0] == exc
    assert knowledge.matching_exception(finding("limits.missing", API), [exc])[0] is None
    with pytest.raises(InvalidInput, match="explanation"):
        knowledge.create_exception(
            store, environment_id=ENV, condition="c", scope=API, explanation="  ", approved_by="u"
        )


def test_expired_and_revoked_exceptions_are_named_not_applied(store: Store) -> None:
    expired = knowledge.create_exception(
        store,
        environment_id=ENV,
        condition="replicas.single",
        scope=SCHED,
        explanation="x",
        approved_by="u",
        expires_at=records.now() - timedelta(days=1),
    )
    exc, note = knowledge.matching_exception(finding("replicas.single", SCHED), [expired])
    assert exc is None
    assert note is not None
    assert "expired on" in note
    fresh = knowledge.create_exception(
        store, environment_id=ENV, condition="replicas.single", scope=SCHED, explanation="x", approved_by="u"
    )
    revoked = knowledge.revoke_exception(store, fresh, "user:boss", "not true any more")
    assert revoked.revoked_by == "user:boss"
    assert revoked.revisions[-1].change == "revoked: not true any more"
    exc, note = knowledge.matching_exception(finding("replicas.single", SCHED), [revoked])
    assert exc is None
    assert note is not None
    assert "revoked on" in note
    assert knowledge.revoke_exception(store, revoked, "u", "again") == revoked
    with pytest.raises(InvestigationStateError):
        knowledge.revise_exception(store, revoked, "u", "x", explanation="y")


def test_revisions_keep_the_history(store: Store) -> None:
    exc = knowledge.create_exception(
        store, environment_id=ENV, condition="c", scope=SCHED, explanation="first", approved_by="u"
    )
    later = records.now() + timedelta(days=30)
    revised = knowledge.revise_exception(
        store, exc, "user:u2", "review date set", explanation="second", review_at=later
    )
    assert revised.explanation == "second"
    assert revised.review_at == later
    assert revised.revisions[0].before["explanation"] == "first"
    assert revised.revisions[0].author == "user:u2"
    assert store.get(records.AcceptedException, exc.id).revisions == revised.revisions


def test_notes_in_scope_and_revisions(store: Store) -> None:
    whole = knowledge.create_note(
        store, environment_id=ENV, kind="constraint", text="no egress", author="user:u"
    )
    ns = knowledge.create_note(
        store,
        environment_id=ENV,
        kind="ownership",
        text="team shop",
        author="user:u",
        scope=records.ResourceRef(kind="", namespace="shop", name=""),
    )
    one = knowledge.create_note(
        store, environment_id=ENV, kind="criticality", text="tier 1", author="user:u", scope=API
    )
    other = knowledge.create_note(
        store,
        environment_id=ENV,
        kind="criticality",
        text="tier 3",
        author="user:u",
        scope=records.ResourceRef(kind="Deployment", namespace="other", name="x"),
    )
    found = {n.id for n in knowledge.notes_in_scope(store, ENV, [API], ["shop"])}
    assert found == {whole.id, ns.id, one.id}
    assert other.id not in found
    assert {n.id for n in knowledge.notes_in_scope(store, ENV, [], ["other"])} == {whole.id, other.id}
    revised = knowledge.revise_note(store, one, "user:u2", "tier 2")
    assert revised.text == "tier 2"
    assert revised.revisions[0].before == {"text": "tier 1"}
    with pytest.raises(InvalidInput):
        knowledge.create_note(store, environment_id=ENV, kind="lesson", text=" ", author="u")
