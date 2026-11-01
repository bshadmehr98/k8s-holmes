"""Saved investigation routes (design §12): save, list, run now, schedule, read the runs and comparisons."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes import saved as saved_mod
from k8sholmes.api import Api, Request, Response, dump, route
from k8sholmes.errors import InvalidInput
from k8sholmes.records import (
    Budgets,
    Environment,
    Investigation,
    Notification,
    Report,
    SavedInvestigation,
    Schedule,
    Scope,
    User,
)


def _saved(api: Api, req: Request, saved_id: str, role: Any) -> tuple[User, SavedInvestigation]:
    api.user(req)
    saved = api.app.store.get(SavedInvestigation, saved_id)
    user, _ = api.env(req, saved.environment_id, role)
    return user, saved


def _schedule(body: dict[str, Any], current: Schedule) -> Schedule:
    raw = cast(dict[str, Any], body.get("schedule") or {})
    return current.model_copy(
        update={
            "every_hours": float(raw["every_hours"]) if raw.get("every_hours") else None,
            "daily_at": str(raw["daily_at"]) if raw.get("daily_at") else None,
            "enabled": bool(raw.get("enabled", current.enabled)),
        }
    )


def _notification(body: dict[str, Any], current: Notification) -> Notification:
    raw = cast(dict[str, Any], body.get("notification") or {})
    return current.model_copy(
        update={
            "webhook_url_env": str(raw["webhook_url_env"]) if raw.get("webhook_url_env") else None,
            "frequency": str(raw.get("frequency") or current.frequency),
        }
    )


@route("GET", "/api/saved")
def list_saved(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "viewer")
    rows = api.app.store.find(SavedInvestigation, {"environment_id": env.id}, order_by="created_at DESC")
    return Response(200, {"saved": [dump(s) for s in rows]})


@route("POST", "/api/saved")
def create_saved(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    user, env = api.env(req, str(body.get("environment_id", "")), "investigator")
    saved = SavedInvestigation(
        environment_id=env.id,
        question=str(body.get("question", "")),
        scope=Scope(namespaces=[str(n) for n in cast(list[Any], body.get("namespaces") or [])]),
        period_rule=str(body.get("period_rule") or "the last 24 hours"),
        model_endpoint_id=cast(str | None, body.get("model_endpoint_id")) or env.settings.default_model_id,
        budgets=Budgets.model_validate(body.get("budgets") or {}),
        schedule=_schedule(body, Schedule()),
        notification=_notification(body, Notification()),
        created_by=user.id,
    )
    saved = saved_mod.create_saved(api.app.store, saved)
    api.app.auth.audit(
        user.id, "saved_investigation_created", environment_id=env.id, subject=f"saved:{saved.id}"
    )
    return Response(201, {"saved": dump(saved)})


@route("PATCH", "/api/saved/(?P<id>[^/]+)")
def edit_saved(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, saved = _saved(api, req, params["id"], "investigator")
    body = api.body(req)
    updates: dict[str, Any] = {}
    if "schedule" in body:
        updates["schedule"] = _schedule(body, saved.schedule)
    if "notification" in body:
        updates["notification"] = _notification(body, saved.notification)
    if body.get("question"):
        updates["question"] = str(body["question"])
    if not updates:
        raise InvalidInput("nothing to change: pass schedule, notification or question")
    saved = saved_mod.create_saved(api.app.store, saved.model_copy(update=updates))
    api.app.auth.audit(
        user.id,
        "saved_investigation_changed",
        environment_id=saved.environment_id,
        subject=f"saved:{saved.id}",
    )
    return Response(200, {"saved": dump(saved)})


@route("POST", "/api/saved/(?P<id>[^/]+)/run")
def run_saved(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, saved = _saved(api, req, params["id"], "investigator")
    env = api.app.store.get(Environment, saved.environment_id)
    api.app.model_for(saved.model_endpoint_id)
    inv = saved_mod.start_run(api.app.store, api.app.engine.start, saved, env, user)
    api.runner.run(inv.id, user)
    return Response(201, {"investigation": dump(inv)})


@route("GET", "/api/saved/(?P<id>[^/]+)")
def read_saved(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, saved = _saved(api, req, params["id"], "viewer")
    store = api.app.store
    runs = [dump(i) for i in store.get_many(Investigation, saved.runs)]
    comparisons = [dump(saved_mod.compare_runs(store, saved, run_id)) for run_id in saved.runs]
    reports = [
        {k: v for k, v in dump(r).items() if k != "sections"}
        for r in store.find(Report, {"kind": "comparison", "subject_id": saved.id}, order_by="revision")
    ]
    return Response(200, {"saved": dump(saved), "runs": runs, "comparisons": comparisons, "reports": reports})
