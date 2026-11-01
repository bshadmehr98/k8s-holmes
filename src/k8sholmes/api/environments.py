"""Environment and capability routes (design §4, §5.4): connecting, the inventory, settings, the catalog,
testing a capability and its execution history."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes.api import Api, Request, Response, dump, route
from k8sholmes.capabilities import Context
from k8sholmes.environments import connect, create_environment, take_inventory
from k8sholmes.records import Call, CapabilitySettings, Evidence, Investigation

# Calls have no environment of their own: they belong through their investigation or, for tests from the
# Capabilities page, through the evidence they produced. Over-fetch so SHOWN_CALLS survive the filter.
RECENT_CALLS, SHOWN_CALLS = 200, 50


@route("GET", "/api/environments")
def environments(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.user(req)
    return Response(200, {"environments": [dump(e) for e in api.visible_environments(user)]})


@route("POST", "/api/environments")
def add_environment(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.admin(req)
    body = api.body(req)
    env = create_environment(
        api.app.store,
        name=str(body.get("name", "")),
        context=str(body.get("context", "")),
        kubeconfig=body.get("kubeconfig") or None,
    )
    api.app.auth.audit(user.id, "environment_created", environment_id=env.id)
    return Response(201, {"environment": dump(env)})


@route("POST", "/api/environments/(?P<id>[^/]+)/connect")
def connect_environment(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, env = api.env(req, params["id"], "admin")
    env = connect(api.app.store, env, api.app.cluster_for(env))
    api.app.auth.audit(
        user.id,
        "environment_connected",
        environment_id=env.id,
        details={"server_version": env.server_version},
    )
    return Response(200, {"environment": dump(env)})


@route("POST", "/api/environments/(?P<id>[^/]+)/inventory")
def inventory(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, params["id"], "investigator")
    taken = take_inventory(api.app.store, env, api.app.cluster_for(env), [])
    summary = {
        "id": taken.id,
        "items": len(taken.items),
        "relationships": len(taken.relationships),
        "unreadable": taken.unreadable,
    }
    return Response(200, {"inventory": summary})


@route("PATCH", "/api/environments/(?P<id>[^/]+)/settings")
def environment_settings(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, env = api.env(req, params["id"], "admin")
    body = api.body(req)
    allowed = {"probes_enabled", "changes_authorized_by", "default_model_id", "retention"}
    changes = {k: v for k, v in body.items() if k in allowed}
    settings = env.settings.model_validate({**dump(env.settings), **changes})
    env = env.model_copy(update={"settings": settings})
    api.app.store.put(env)
    api.app.auth.audit(user.id, "environment_settings_changed", environment_id=env.id, details=changes)
    return Response(200, {"environment": dump(env)})


@route("PATCH", "/api/environments/(?P<id>[^/]+)/capabilities/(?P<cap>[^/]+)")
def capability_settings(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, env = api.env(req, params["id"], "admin")  # capabilities are administered (design §14.1)
    cap = api.app.registry.get(params["cap"])
    settings = CapabilitySettings.model_validate(api.body(req))
    capabilities = {**env.settings.capabilities, cap.id: settings}
    env = env.model_copy(update={"settings": env.settings.model_copy(update={"capabilities": capabilities})})
    api.app.store.put(env)
    api.app.auth.audit(
        user.id,
        "capability_settings_changed",
        environment_id=env.id,
        subject=f"capability:{cap.id}",
        details=dump(settings),
    )
    return Response(200, {"settings": dump(settings)})


@route("GET", "/api/capabilities")
def capabilities(api: Api, req: Request, params: dict[str, str]) -> Response:
    user = api.user(req)
    env = None
    if req.query.get("env"):
        _, env = api.env(req, req.query["env"], "viewer")
    rows: list[dict[str, Any]] = []
    for cap in api.app.registry.all():
        row = cap.describe()
        if env is not None:
            refusal = api.app.registry.refusal(cap, user, env, api.app.auth)
            row["available"] = refusal is None
            row["why_not"] = refusal.reason if refusal else None
            row["settings"] = dump(api.app.registry.settings_for(cap, env))
        rows.append(row)
    return Response(200, {"capabilities": rows, "broken_extensions": api.app.registry.broken})


@route("POST", "/api/capabilities/(?P<id>[^/]+)/test")
def test_capability(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    user, env = api.env(req, str(body.get("environment_id", "")), "investigator")
    cap = api.app.registry.get(params["id"])
    ctx = Context(environment=env, cluster=api.app.cluster_for(env), store=api.app.store, user=user)
    call, evidence = api.app.registry.execute(
        cap.id, cast(dict[str, Any], body.get("inputs") or {}), ctx, api.app.auth
    )
    with api.app.store.transaction():
        api.app.store.put(call)
        if evidence is not None:
            api.app.store.put(evidence.model_copy(update={"retention": "short"}))
    api.app.auth.audit(
        user.id,
        "capability_tested",
        environment_id=env.id,
        subject=f"capability:{cap.id}",
        details={"status": call.status},
    )
    return Response(200, {"call": dump(call), "evidence": dump(evidence) if evidence else None})


@route("GET", "/api/capabilities/(?P<id>[^/]+)/calls")
def capability_calls(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "investigator")
    calls = api.app.store.find(Call, {"capability_id": params["id"]}, order_by="at DESC", limit=RECENT_CALLS)
    in_env = {i.id for i in api.app.store.find(Investigation, {"environment_id": env.id})}
    evidence_env = {
        e.call_id for e in api.app.store.find(Evidence, {"environment_id": env.id, "source": params["id"]})
    }
    rows = [c for c in calls if c.investigation_id in in_env or c.id in evidence_env][:SHOWN_CALLS]
    return Response(200, {"calls": [dump(c) for c in rows]})
