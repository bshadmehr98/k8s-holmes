"""Knowledge notes and accepted exceptions: scope, matching, expiry, revocation (design §9)."""

from __future__ import annotations

from datetime import datetime

from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import AcceptedException, Finding, Knowledge, KnowledgeKind, ResourceRef, Revision, now
from k8sholmes.store import Store


def scope_touches(scope: ResourceRef | None, refs: list[ResourceRef], namespaces: list[str]) -> bool:
    """Whether a note's or exception's scope concerns any of `refs` (or the namespaces when no refs)."""
    if scope is None:
        return True
    if not refs and not namespaces:
        return True
    if scope.namespace and not scope.name and not scope.kind:
        return scope.namespace in namespaces or any(r.namespace == scope.namespace for r in refs)
    for r in refs:
        same_ns = scope.namespace in (None, r.namespace)
        same_kind = not scope.kind or scope.kind.lower() == r.kind.lower()
        same_name = not scope.name or scope.name == r.name
        if same_ns and same_kind and same_name:
            return True
    return not refs and bool(scope.namespace) and scope.namespace in namespaces


def notes_in_scope(
    store: Store, environment_id: str, refs: list[ResourceRef], namespaces: list[str]
) -> list[Knowledge]:
    notes = store.find(Knowledge, {"environment_id": environment_id})
    return [n for n in notes if scope_touches(n.scope, refs, namespaces)]


def exceptions_in_scope(
    store: Store, environment_id: str, refs: list[ResourceRef], namespaces: list[str]
) -> list[AcceptedException]:
    found = store.find(AcceptedException, {"environment_id": environment_id})
    return [e for e in found if scope_touches(e.scope, refs, namespaces)]


def is_active(exception: AcceptedException, at: datetime | None = None) -> bool:
    at = at or now()
    return exception.revoked_at is None and (exception.expires_at is None or exception.expires_at > at)


def covers(exception: AcceptedException, ref: ResourceRef) -> bool:
    """Whether an exception's scope covers this resource: the one matching rule (design §9.1)."""
    scope = exception.scope
    if scope.kind.lower() != ref.kind.lower() or scope.namespace != ref.namespace:
        return False
    return exception.applies_to == "namespace" or scope.name == ref.name


def matching_exception(
    finding: Finding, exceptions: list[AcceptedException], at: datetime | None = None
) -> tuple[AcceptedException | None, str | None]:
    """The exception that covers this finding exactly (design §9.1), or a note about one that would have.

    Exact: same environment, same condition, every resource of the finding covered. Expired or revoked
    exceptions never apply; the note names them so the finding can say so.
    """
    at = at or now()
    note: str | None = None
    for exc in exceptions:
        if exc.environment_id != finding.environment_id or exc.condition != finding.condition:
            continue
        if not finding.resources or not all(covers(exc, r) for r in finding.resources):
            continue
        if is_active(exc, at):
            return exc, None
        if exc.revoked_at is not None:
            note = f"an exception ({exc.id}) was revoked on {exc.revoked_at.date().isoformat()}"
        elif exc.expires_at is not None:
            note = f"an exception ({exc.id}) expired on {exc.expires_at.date().isoformat()}"
    return None, note


def create_exception(store: Store, exc: AcceptedException) -> AcceptedException:
    """Store an exception after the two checks the record itself cannot make (design §9.1)."""
    if not exc.explanation.strip():
        raise InvalidInput("an exception needs an explanation: why is this intentional?")
    if exc.applies_to == "resource" and not exc.scope.name:
        raise InvalidInput(
            "an exception on a resource needs its name; choose applies_to=namespace for a whole namespace"
        )
    exc = exc.model_copy(update={"explanation": exc.explanation.strip()})
    store.put(exc)
    return exc


EXCEPTION_EDITABLE = {"explanation", "review_at", "expires_at", "reconsider_when", "triggers", "applies_to"}


def revise_exception(
    store: Store, exc: AcceptedException, author: str, change: str, **updates: object
) -> AcceptedException:
    """Edit an exception; every edit is a revision (design §9.1)."""
    if exc.revoked_at is not None:
        raise InvestigationStateError("a revoked exception is history; create a new one instead")
    bad = set(updates) - EXCEPTION_EDITABLE
    if bad:
        raise InvalidInput(f"these fields of an exception are not edited: {', '.join(sorted(bad))}")
    before = {k: getattr(exc, k) for k in updates}
    revision = Revision(
        at=now(), author=author, change=change, before={k: _plain(v) for k, v in before.items()}
    )
    revised = exc.model_copy(update={**updates, "revisions": [*exc.revisions, revision]})
    store.put(revised)
    return revised


def revoke_exception(store: Store, exc: AcceptedException, by: str, reason: str) -> AcceptedException:
    if exc.revoked_at is not None:
        return exc
    revision = Revision(at=now(), author=by, change=f"revoked: {reason}")
    revoked = exc.model_copy(
        update={
            "revoked_at": now(),
            "revoked_by": by,
            "revocation_reason": reason,
            "revisions": [*exc.revisions, revision],
        }
    )
    store.put(revoked)
    return revoked


def _plain(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[attr-defined]
    return value


def create_note(
    store: Store,
    *,
    environment_id: str,
    kind: KnowledgeKind,
    text: str,
    author: str,
    scope: ResourceRef | None = None,
    source: str = "user",
) -> Knowledge:
    if not text.strip():
        raise InvalidInput("a knowledge note needs text")
    note = Knowledge(
        environment_id=environment_id, kind=kind, scope=scope, text=text.strip(), author=author, source=source
    )
    store.put(note)
    return note


def revise_note(store: Store, note: Knowledge, author: str, text: str) -> Knowledge:
    revision = Revision(at=now(), author=author, change="text edited", before={"text": note.text})
    revised = note.model_copy(
        update={"text": text.strip(), "updated_at": now(), "revisions": [*note.revisions, revision]}
    )
    store.put(revised)
    return revised
