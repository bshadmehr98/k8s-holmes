"""Investigation routes (design §6.4, §15.2): start, read, the event stream, control, evidence, findings."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes import findings as findings_mod
from k8sholmes import knowledge
from k8sholmes.api import Api, Request, Response, dump, parse_when, route
from k8sholmes.engine import RESUMABLE
from k8sholmes.errors import InvalidInput, InvestigationStateError
from k8sholmes.records import (
    AcceptedException,
    Call,
    Environment,
    Evidence,
    Finding,
    Investigation,
    Message,
    Role,
    Scope,
    Step,
    User,
)
from k8sholmes.reports import investigation_json, report_text


def _investigation(
    api: Api, req: Request, investigation_id: str, role: Role
) -> tuple[User, Investigation, Environment]:
    api.user(req)  # the session before anything is looked up (design §14.1)
    inv = api.app.store.get(Investigation, investigation_id)
    user, env = api.env(req, inv.environment_id, role)
    return user, inv, env


@route("POST", "/api/investigations")
def start_investigation(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    user, env = api.env(req, str(body.get("environment_id", "")), "investigator")
    key = str(body.get("idempotency_key") or "")
    scoped = f"{user.id}:{env.id}:{key}" if key else None
    existing = api.runner.by_key(scoped) if scoped else None
    if existing:
        found = api.app.store.get(Investigation, existing)
        if found.environment_id == env.id:
            return Response(200, {"investigation": dump(found)})
    scope = Scope(namespaces=[str(n) for n in cast(list[Any], body.get("namespaces") or [])])
    endpoint_id = body.get("model_endpoint_id") or env.settings.default_model_id
    api.app.model_for(
        endpoint_id
    )  # raises before anything starts when the endpoint is missing or untested (§13.2)
    inv = api.app.engine.start(
        environment=env,
        question=str(body.get("question", "")),
        user=user,
        scope=scope,
        model_endpoint_id=endpoint_id,
    )
    api.runner.run(inv.id, user, key=scoped)
    return Response(201, {"investigation": dump(inv)})


@route("GET", "/api/investigations")
def investigations(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "viewer")
    rows = api.app.store.find(
        Investigation, {"environment_id": env.id}, order_by="created_at DESC", limit=200
    )
    out = [{**dump(i), "findings": api.app.store.count(Finding, {"investigation_id": i.id})} for i in rows]
    return Response(200, {"investigations": out})


@route("GET", "/api/investigations/(?P<id>[^/]+)")
def investigation(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, inv, env = _investigation(api, req, params["id"], "viewer")
    evidence = [
        {k: v for k, v in dump(e).items() if k != "data"}
        for e in api.app.store.find(Evidence, {"investigation_id": inv.id}, order_by="collected_at")
    ]
    bundle = {
        "investigation": dump(inv),
        "environment": {"id": env.id, "name": env.name},
        "steps": [dump(s) for s in api.app.store.find(Step, {"investigation_id": inv.id}, order_by="seq")],
        "calls": [dump(c) for c in api.app.store.find(Call, {"investigation_id": inv.id}, order_by="at")],
        "evidence": evidence,
        "findings": [
            dump(f) for f in api.app.store.find(Finding, {"investigation_id": inv.id}, order_by="created_at")
        ],
        "messages": [
            dump(m)
            for m in api.app.store.find(Message, {"investigation_id": inv.id}, order_by="seq")
            if m.role != "tool"
        ],
        "running": api.runner.is_running(inv.id),
    }
    return Response(200, bundle)


@route("GET", "/api/investigations/(?P<id>[^/]+)/events")
def investigation_events(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, inv, _ = _investigation(api, req, params["id"], "viewer")
    return Response(200, None, content_type="text/event-stream", stream=inv.id)


@route("POST", "/api/investigations/(?P<id>[^/]+)/cancel")
def cancel(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, inv, _ = _investigation(api, req, params["id"], "investigator")
    return Response(200, {"investigation": dump(api.app.engine.cancel(inv.id, user))})


@route("POST", "/api/investigations/(?P<id>[^/]+)/resume")
def resume(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, inv, _ = _investigation(api, req, params["id"], "investigator")
    if inv.status not in RESUMABLE:
        raise InvestigationStateError(
            f"investigation {inv.id} is {inv.status}; only {', '.join(RESUMABLE)} resume"
        )
    api.app.auth.audit(
        user.id,
        "investigation_resume_requested",
        environment_id=inv.environment_id,
        subject=f"investigation:{inv.id}",
    )
    api.runner.run(inv.id, user)
    return Response(202, {"investigation": dump(api.app.store.get(Investigation, inv.id))})


@route("POST", "/api/investigations/(?P<id>[^/]+)/follow_up")
def follow_up(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, inv, _ = _investigation(api, req, params["id"], "investigator")
    message = str(api.body(req).get("message", "")).strip()
    if not message:
        raise InvalidInput("the message is empty")
    api.runner.follow_up(inv.id, user, message)
    return Response(202, {"investigation": dump(api.app.store.get(Investigation, inv.id))})


@route("GET", "/api/investigations/(?P<id>[^/]+)/report")
def report(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, inv, _ = _investigation(api, req, params["id"], "viewer")
    return Response(200, report_text(api.app, inv), content_type="text/markdown; charset=utf-8")


@route("GET", "/api/investigations/(?P<id>[^/]+)/export")
def export(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, inv, _ = _investigation(api, req, params["id"], "viewer")
    api.app.auth.audit(
        user.id, "export", environment_id=inv.environment_id, subject=f"investigation:{inv.id}"
    )
    payload = {"format_version": "1", **investigation_json(api.app, inv)}
    return Response(200, payload, headers={"Content-Disposition": f'attachment; filename="{inv.id}.json"'})


@route("GET", "/api/evidence/(?P<id>[^/]+)")
def evidence(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    ev = api.app.store.get(Evidence, params["id"])
    api.env(req, ev.environment_id, "viewer")
    return Response(200, {"evidence": dump(ev)})


def _finding(api: Api, req: Request, finding_id: str) -> tuple[User, Finding]:
    api.user(req)
    finding = api.app.store.get(Finding, finding_id)
    user, _ = api.env(req, finding.environment_id, "investigator")
    return user, finding


@route("PATCH", "/api/findings/(?P<id>[^/]+)")
def edit_finding(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, finding = _finding(api, req, params["id"])
    body = api.body(req)
    if body.get("state"):
        state = cast(Any, str(body["state"]))
        finding = findings_mod.set_state(
            api.app.store, finding, state, author=f"user:{user.id}", note=str(body.get("note", ""))
        )
        api.app.auth.audit(
            user.id,
            "finding_state_changed",
            environment_id=finding.environment_id,
            subject=f"finding:{finding.id}",
            details={"state": finding.state},
        )
    if body.get("edits"):
        finding = findings_mod.revise(
            api.app.store,
            finding,
            author=f"user:{user.id}",
            change="edited in the interface",
            **cast(dict[str, Any], body["edits"]),
        )
        api.app.auth.audit(
            user.id, "finding_edited", environment_id=finding.environment_id, subject=f"finding:{finding.id}"
        )
    return Response(200, {"finding": dump(finding)})


@route("POST", "/api/findings/(?P<id>[^/]+)/accept")
def accept_finding(api: Api, req: Request, params: dict[str, str]) -> Response:
    """Accept a finding as intentional: the exception and the state change happen together (design §9.1)."""
    user, finding = _finding(api, req, params["id"])
    body = api.body(req)
    if len(finding.resources) != 1:
        raise InvalidInput("accept a finding on exactly one resource; this one names several or none")
    if "accepted_risk" not in findings_mod.TRANSITIONS[finding.state]:
        raise InvestigationStateError(f"a finding cannot go from {finding.state} to accepted_risk")
    exc = AcceptedException(
        environment_id=finding.environment_id,
        condition=finding.condition,
        scope=finding.resources[0],
        explanation=str(body.get("explanation", "")),
        approved_by=f"user:{user.id}",
        review_at=parse_when(body.get("review_at")),
        expires_at=parse_when(body.get("expires_at")),
        reconsider_when=str(body.get("reconsider_when", "")),
        triggers=cast(Any, body.get("triggers") or []),
        finding_id=finding.id,
    )
    with api.app.store.transaction():
        exc = knowledge.create_exception(api.app.store, exc)
        finding = findings_mod.set_state(
            api.app.store, finding, "accepted_risk", author=f"user:{user.id}", note=f"exception {exc.id}"
        )
        finding = finding.model_copy(update={"exception_id": exc.id})
        api.app.store.put(finding)
    api.app.auth.audit(
        user.id,
        "exception_created",
        environment_id=finding.environment_id,
        subject=f"exception:{exc.id}",
        details={"condition": exc.condition, "scope": dump(exc.scope)},
    )
    return Response(201, {"exception": dump(exc), "finding": dump(finding)})
