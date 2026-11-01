"""Report routes (design §10, §13.4): generate a revision, read it, edit it, share it, download it; the live
summary of a running investigation; the fixed-evidence model comparison."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes import reports
from k8sholmes.api import Api, Request, Response, dump, route
from k8sholmes.capabilities import Context
from k8sholmes.errors import InvalidInput, InvestigationStateError, K8sHolmesError
from k8sholmes.records import Finding, Investigation, Report, Role, SavedInvestigation, Share, User
from k8sholmes.saved import compare_runs, comparison_report

FINISHED = ("completed", "partial", "cancelled", "failed", "interrupted")


def _report(api: Api, req: Request, report_id: str, role: Role) -> tuple[User, Report]:
    api.user(req)
    report = api.app.store.get(Report, report_id)
    user, _ = api.env(req, report.environment_id, role)
    return user, report


@route("GET", "/api/reports")
def list_reports(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "viewer")
    rows = api.app.store.find(Report, {"environment_id": env.id}, order_by="generated_at DESC", limit=500)
    return Response(200, {"reports": [{k: v for k, v in dump(r).items() if k != "sections"} for r in rows]})


@route("POST", "/api/reports")
def generate_report(api: Api, req: Request, params: dict[str, str]) -> Response:
    """A new revision of a report of the given kind (design §10)."""
    body = api.body(req)
    user, env = api.env(req, str(body.get("environment_id", "")), "investigator")
    kind, by, store = str(body.get("kind", "")), f"user:{user.id}", api.app.store
    if kind in ("investigation", "incident_review"):
        inv = store.get(Investigation, str(body.get("investigation_id", "")))
        if inv.environment_id != env.id:
            raise InvalidInput("the investigation belongs to another environment")
        make = reports.investigation_report if kind == "investigation" else reports.incident_review
        report = make(store, inv, by=by)
    elif kind == "reliability_review":
        namespaces = [str(n) for n in cast(list[Any], body.get("namespaces") or [])]
        ctx = Context(environment=env, cluster=api.app.cluster_for(env), store=store, user=user)
        report = reports.reliability_review(store, api.app.registry, api.app.auth, ctx, namespaces, by=by)
    elif kind == "comparison":
        saved = store.get(SavedInvestigation, str(body.get("saved_id", "")))
        if saved.environment_id != env.id or not saved.runs:
            raise InvalidInput("the saved investigation belongs to another environment or has not run")
        comparison = compare_runs(store, saved, str(body.get("after_id") or saved.runs[-1]))
        report = comparison_report(store, saved, comparison, by=by)
    else:
        raise InvalidInput("kind is one of investigation, incident_review, reliability_review, comparison")
    details = {"kind": kind, "revision": report.revision}
    api.app.auth.audit(
        user.id, "report_generated", environment_id=env.id, subject=f"report:{report.id}", details=details
    )
    return Response(201, {"report": dump(report)})


@route("GET", "/api/reports/(?P<id>[^/]+)")
def read_report(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, report = _report(api, req, params["id"], "viewer")
    revisions = [
        {
            "id": r.id,
            "revision": r.revision,
            "generated_at": r.generated_at,
            "generated_by": r.generated_by,
            "edits": len(r.edits),
        }
        for r in reports.revisions(api.app.store, report)
    ]
    shares = [dump(s) for s in api.app.store.find(Share, {"report_id": report.id}, order_by="created_at")]
    return Response(200, {"report": dump(report), "revisions": revisions, "shares": shares})


@route("GET", "/api/reports/(?P<id>[^/]+)/markdown")
def report_markdown(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, report = _report(api, req, params["id"], "viewer")
    return Response(200, reports.to_markdown(report), content_type="text/markdown; charset=utf-8")


@route("GET", "/api/reports/(?P<id>[^/]+)/export")
def report_export(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, report = _report(api, req, params["id"], "viewer")
    api.app.auth.audit(user.id, "export", environment_id=report.environment_id, subject=f"report:{report.id}")
    headers = {"Content-Disposition": f'attachment; filename="{report.id}.json"'}
    return Response(200, {"format_version": "1", "report": dump(report)}, headers=headers)


@route("POST", "/api/reports/(?P<id>[^/]+)/edits")
def edit_report(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, report = _report(api, req, params["id"], "investigator")
    body = api.body(req)
    edited = reports.add_edit(
        api.app.store,
        report,
        author=f"user:{user.id}",
        section=str(body.get("section", "")),
        text=str(body.get("text", "")),
    )
    api.app.auth.audit(
        user.id, "report_edited", environment_id=report.environment_id, subject=f"report:{report.id}"
    )
    return Response(200, {"report": dump(edited)})


@route("POST", "/api/reports/(?P<id>[^/]+)/shares")
def share_report(api: Api, req: Request, params: dict[str, str]) -> Response:
    """A view-only link, minted by an administrator (§14.1); the token appears in this response only."""
    user, report = _report(api, req, params["id"], "admin")
    days = int((req.body or {}).get("days") or 7)
    if not 1 <= days <= 90:
        raise InvalidInput("a share lasts between 1 and 90 days")
    share, token = reports.create_share(api.app.store, report, by=f"user:{user.id}", days=days)
    details = {"report_id": report.id, "expires_at": share.expires_at.isoformat()}
    api.app.auth.audit(
        user.id,
        "share_created",
        environment_id=report.environment_id,
        subject=f"share:{share.id}",
        details=details,
    )
    return Response(201, {"share": dump(share), "token": token, "path": f"/#/shared/{token}"})


@route("POST", "/api/shares/(?P<id>[^/]+)/revoke")
def revoke_share(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    share = api.app.store.get(Share, params["id"])
    user, _ = api.env(req, share.environment_id, "admin")
    revoked = reports.revoke_share(api.app.store, share)
    api.app.auth.audit(
        user.id, "share_revoked", environment_id=share.environment_id, subject=f"share:{share.id}"
    )
    return Response(200, {"share": dump(revoked)})


@route("GET", "/api/shared/(?P<token>[^/]+)", public=True)
def shared(api: Api, req: Request, params: dict[str, str]) -> Response:
    """The report behind a share token: no session, view only, nothing else of the environment."""
    report = reports.shared_report(api.app.store, params["token"])
    return Response(200, {"report": dump(report), "shared": True})


@route("GET", "/api/shared/(?P<token>[^/]+)/markdown", public=True)
def shared_markdown(api: Api, req: Request, params: dict[str, str]) -> Response:
    report = reports.shared_report(api.app.store, params["token"])
    return Response(200, reports.to_markdown(report), content_type="text/markdown; charset=utf-8")


@route("GET", "/api/investigations/(?P<id>[^/]+)/live")
def live(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    inv = api.app.store.get(Investigation, params["id"])
    api.env(req, inv.environment_id, "viewer")
    return Response(200, {"live": reports.live_summary(api.app.store, inv)})


@route("POST", "/api/investigations/(?P<id>[^/]+)/compare_models")
def compare_models(api: Api, req: Request, params: dict[str, str]) -> Response:
    """The fixed-evidence comparison (design §13.4): each endpoint concludes on the stored evidence."""
    api.user(req)
    inv = api.app.store.get(Investigation, params["id"])
    user, _ = api.env(req, inv.environment_id, "investigator")
    if inv.status not in FINISHED:
        raise InvestigationStateError(
            f"investigation {inv.id} is {inv.status}; its evidence is still changing"
        )
    endpoint_ids = [str(e) for e in cast(list[Any], api.body(req).get("endpoint_ids") or [])]
    if not endpoint_ids:
        raise InvalidInput("name at least one model endpoint to compare")
    stored = [
        dump(f) for f in api.app.store.find(Finding, {"investigation_id": inv.id}, order_by="created_at")
    ]
    results: list[dict[str, Any]] = []
    for endpoint_id in endpoint_ids:
        try:
            model = api.app.model_for(endpoint_id)
            validated, reply = api.app.engine.conclude_only(inv, model, user)
        except K8sHolmesError as exc:
            results.append({"endpoint_id": endpoint_id, "error": str(exc)})
            continue
        usage = {
            "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "duration_ms": reply.duration_ms,
        }
        results.append(
            {
                "endpoint_id": endpoint_id,
                "model": model.name,
                "answer": validated.answer,
                "findings": validated.findings,
                "limitations": validated.limitations,
                "problems": validated.problems,
                "usage": usage,
            }
        )
    details = {"endpoints": endpoint_ids}
    api.app.auth.audit(
        user.id,
        "models_compared",
        environment_id=inv.environment_id,
        subject=f"investigation:{inv.id}",
        details=details,
    )
    return Response(200, {"kind": "fixed_evidence", "stored_findings": stored, "results": results})
