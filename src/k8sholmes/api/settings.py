"""Settings routes: model endpoints and their capability tests, users and grants, the audit log (§13, §14)."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes.api import Api, Request, Response, dump, route
from k8sholmes.errors import InvalidInput
from k8sholmes.models import OpenAICompatible, api_key_for, price_of, run_capability_tests, write_secret
from k8sholmes.records import AuditEntry, Environment, ModelCall, ModelEndpoint, ModelLimits, ModelPrice, User


def _endpoint_row(api: Api, endpoint: ModelEndpoint) -> dict[str, Any]:
    calls = api.app.store.find(ModelCall, {"endpoint_id": endpoint.id})
    prompt, completion = sum(c.prompt_tokens for c in calls), sum(c.completion_tokens for c in calls)
    row = dump(endpoint)
    row["has_key"] = api_key_for(endpoint, api.app.home) is not None
    row["usage"] = {
        "calls": len(calls),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "price": price_of(endpoint, prompt, completion),
    }
    return row


@route("GET", "/api/models")
def models(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    return Response(
        200,
        {"endpoints": [_endpoint_row(api, e) for e in api.app.store.find(ModelEndpoint, order_by="name")]},
    )


@route("POST", "/api/models")
def add_model(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.admin(req)
    body = api.body(req)
    endpoint = ModelEndpoint(
        name=str(body.get("name", "")).strip() or "endpoint",
        base_url=str(body.get("base_url", "")).strip(),
        model=str(body.get("model", "")).strip(),
        api_key_env=body.get("api_key_env") or None,
        limits=ModelLimits.model_validate(body.get("limits") or {}),
        price=ModelPrice.model_validate(body["price"]) if body.get("price") else None,
        is_default=not api.app.store.find(ModelEndpoint, {"is_default": True}),
    )
    if not endpoint.base_url.startswith(("http://", "https://")) or not endpoint.model:
        raise InvalidInput("a model endpoint needs an http(s) base URL and a model name")
    api.app.store.put(endpoint)
    if body.get("api_key"):
        write_secret(api.app.home, endpoint.id, str(body["api_key"]))
    api.app.auth.audit(
        user.id,
        "model_endpoint_added",
        subject=f"model:{endpoint.id}",
        details={"base_url": endpoint.base_url, "model": endpoint.model},
    )
    return Response(201, {"endpoint": _endpoint_row(api, endpoint)})


@route("PATCH", "/api/models/(?P<id>[^/]+)")
def edit_model(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.admin(req)
    endpoint = api.app.store.get(ModelEndpoint, params["id"])
    body = api.body(req)
    if body.get("is_default"):
        for other in api.app.store.find(ModelEndpoint, {"is_default": True}):
            api.app.store.put(other.model_copy(update={"is_default": False}))
        endpoint = endpoint.model_copy(update={"is_default": True})
    if "limits" in body:
        endpoint = endpoint.model_copy(update={"limits": ModelLimits.model_validate(body["limits"])})
    api.app.store.put(endpoint)
    api.app.auth.audit(user.id, "model_endpoint_changed", subject=f"model:{endpoint.id}")
    return Response(200, {"endpoint": _endpoint_row(api, endpoint)})


@route("POST", "/api/models/(?P<id>[^/]+)/test")
def test_model(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.admin(req)
    endpoint = api.app.store.get(ModelEndpoint, params["id"])
    if endpoint.base_url.startswith("demo://"):
        raise InvalidInput("the demo investigator is not a model endpoint; it needs no tests")
    results = run_capability_tests(OpenAICompatible(endpoint, api_key_for(endpoint, api.app.home)))
    endpoint = endpoint.model_copy(update={"tests": results})
    api.app.store.put(endpoint)
    api.app.auth.audit(
        user.id,
        "model_endpoint_tested",
        subject=f"model:{endpoint.id}",
        details={k: v.status for k, v in results.items()},
    )
    return Response(
        200, {"endpoint": _endpoint_row(api, endpoint), "tests": {k: dump(v) for k, v in results.items()}}
    )


@route("GET", "/api/users")
def users(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.admin(req)
    rows = [
        {k: v for k, v in dump(u).items() if k != "password_hash"}
        for u in api.app.store.find(User, order_by="username")
    ]
    return Response(200, {"users": rows})


@route("POST", "/api/users")
def add_user(api: Api, req: Request, params: dict[str, str]) -> Response:
    admin = api.admin(req)
    body = api.body(req)
    user = api.app.auth.create_user(
        str(body.get("username", "")), str(body.get("password", "")), is_admin=bool(body.get("is_admin"))
    )
    api.app.auth.audit(admin.id, "user_created", subject=f"user:{user.id}")
    return Response(201, {"user": {"id": user.id, "username": user.username, "is_admin": user.is_admin}})


@route("PUT", "/api/users/(?P<id>[^/]+)/grants")
def set_grant(api: Api, req: Request, params: dict[str, str]) -> Response:
    admin = api.admin(req)
    body = api.body(req)
    user = api.app.store.get(User, params["id"])
    env = api.app.store.get(Environment, str(body.get("environment_id", "")))
    role = body.get("role") or None
    if role not in (None, "viewer", "investigator", "operator"):
        raise InvalidInput("the role is viewer, investigator, operator or empty to remove access")
    user = api.app.auth.set_grant(user, env.id, cast(Any, role))
    api.app.auth.audit(
        admin.id, "grant_changed", environment_id=env.id, subject=f"user:{user.id}", details={"role": role}
    )
    return Response(200, {"user": {"id": user.id, "grants": [dump(g) for g in user.grants]}})


@route("GET", "/api/audit")
def audit(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.admin(req)
    where = {"environment_id": req.query["env"]} if req.query.get("env") else None
    entries = api.app.store.find(AuditEntry, where, order_by="at DESC", limit=200)
    names = {u.id: u.username for u in api.app.store.find(User)}
    return Response(
        200, {"entries": [{**dump(a), "user": names.get(a.user_id or "", a.user_id)} for a in entries]}
    )
