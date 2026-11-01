"""Knowledge and exception routes (design §9)."""

from __future__ import annotations

from typing import Any

from k8sholmes import knowledge
from k8sholmes.api import Api, Request, Response, dump, parse_when, route
from k8sholmes.records import AcceptedException, Knowledge, ResourceRef


@route("GET", "/api/knowledge")
def knowledge_list(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "viewer")
    notes = api.app.store.find(Knowledge, {"environment_id": env.id}, order_by="created_at DESC")
    exceptions = api.app.store.find(AcceptedException, {"environment_id": env.id})
    return Response(200, {"knowledge": [dump(n) for n in notes], "exceptions": [dump(e) for e in exceptions]})


@route("POST", "/api/knowledge")
def add_knowledge(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    user, env = api.env(req, str(body.get("environment_id", "")), "investigator")
    scope = ResourceRef.model_validate(body["scope"]) if body.get("scope") else None
    note = knowledge.create_note(
        api.app.store,
        environment_id=env.id,
        kind=body.get("kind", "lesson"),
        text=str(body.get("text", "")),
        author=f"user:{user.id}",
        scope=scope,
    )
    api.app.auth.audit(user.id, "knowledge_added", environment_id=env.id, subject=f"knowledge:{note.id}")
    return Response(201, {"knowledge": dump(note)})


@route("PATCH", "/api/knowledge/(?P<id>[^/]+)")
def edit_knowledge(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    note = api.app.store.get(Knowledge, params["id"])
    user, _ = api.env(req, note.environment_id, "investigator")
    note = knowledge.revise_note(api.app.store, note, f"user:{user.id}", str(api.body(req).get("text", "")))
    return Response(200, {"knowledge": dump(note)})


def _exception(api: Api, req: Request, exception_id: str) -> tuple[Any, AcceptedException]:
    api.user(req)
    exc = api.app.store.get(AcceptedException, exception_id)
    user, _ = api.env(req, exc.environment_id, "investigator")
    return user, exc


@route("PATCH", "/api/exceptions/(?P<id>[^/]+)")
def edit_exception(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, exc = _exception(api, req, params["id"])
    body = api.body(req)
    updates: dict[str, Any] = {
        k: v for k, v in body.items() if k in ("explanation", "reconsider_when", "triggers", "applies_to")
    }
    for key in ("review_at", "expires_at"):
        if key in body:
            updates[key] = parse_when(body[key])
    exc = knowledge.revise_exception(
        api.app.store, exc, f"user:{user.id}", "edited in the interface", **updates
    )
    api.app.auth.audit(
        user.id,
        "exception_edited",
        environment_id=exc.environment_id,
        subject=f"exception:{exc.id}",
        details={"fields": sorted(updates)},
    )
    return Response(200, {"exception": dump(exc)})


@route("POST", "/api/exceptions/(?P<id>[^/]+)/revoke")
def revoke_exception(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, exc = _exception(api, req, params["id"])
    exc = knowledge.revoke_exception(
        api.app.store, exc, f"user:{user.id}", str(api.body(req).get("reason", ""))
    )
    api.app.auth.audit(
        user.id,
        "exception_revoked",
        environment_id=exc.environment_id,
        subject=f"exception:{exc.id}",
        details={"reason": exc.revocation_reason},
    )
    return Response(200, {"exception": dump(exc)})
