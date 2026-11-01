"""Proposal routes (design §11): propose from findings, move the state by hand, verify by rerunning."""

from __future__ import annotations

from typing import Any, cast

from k8sholmes import proposals
from k8sholmes.api import Api, Request, Response, dump, route
from k8sholmes.capabilities import Context
from k8sholmes.errors import InvalidInput
from k8sholmes.records import Environment, Finding, Investigation, Proposal, Role, User

ROLE_FOR_STATE: dict[str, Role] = {
    "proposed": "investigator",
    "rejected": "investigator",
    "approved": "operator",
    "applied": "operator",
}


def _proposal(api: Api, req: Request, proposal_id: str, role: Role) -> tuple[User, Proposal]:
    api.user(req)
    proposal = api.app.store.get(Proposal, proposal_id)
    user, _ = api.env(req, proposal.environment_id, role)
    return user, proposal


@route("GET", "/api/proposals")
def list_proposals(api: Api, req: Request, params: dict[str, str]) -> Response:
    _, env = api.env(req, req.query.get("env", ""), "viewer")
    where: dict[str, Any] = {"environment_id": env.id}
    if req.query.get("investigation"):
        where["investigation_id"] = req.query["investigation"]
    rows = api.app.store.find(Proposal, where, order_by="created_at DESC", limit=500)
    return Response(200, {"proposals": [dump(p) for p in rows]})


@route("POST", "/api/proposals")
def create_proposal(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    body = api.body(req)
    inv = api.app.store.get(Investigation, str(body.get("investigation_id", "")))
    user, env = api.env(req, inv.environment_id, "investigator")
    finding_ids = [str(f) for f in cast(list[Any], body.get("finding_ids") or [])]
    for finding in api.app.store.get_many(Finding, finding_ids):
        if finding.investigation_id != inv.id:
            raise InvalidInput(f"finding {finding.id} belongs to another investigation")
    proposal = Proposal(
        environment_id=env.id,
        investigation_id=inv.id,
        finding_ids=finding_ids,
        kind=cast(Any, str(body.get("kind", "task"))),
        title=str(body.get("title", "")),
        change=str(body.get("change", "")),
        intended_effect=str(body.get("intended_effect", "")),
        verification_capabilities=cast(list[dict[str, Any]], body.get("verification_capabilities") or []),
        verification_condition=str(body.get("verification_condition", "")),
        wait_ms=int(body.get("wait_ms") or 0),
        author=f"user:{user.id}",
    )
    proposal = proposals.create_proposal(api.app.store, proposal)
    api.app.auth.audit(user.id, "proposal_created", environment_id=env.id, subject=f"proposal:{proposal.id}")
    return Response(201, {"proposal": dump(proposal)})


@route("PATCH", "/api/proposals/(?P<id>[^/]+)")
def set_proposal_state(api: Api, req: Request, params: dict[str, str]) -> Response:
    api.user(req)
    body = api.body(req)
    state = str(body.get("state", ""))
    if state not in ROLE_FOR_STATE:
        raise InvalidInput(
            "state is one of proposed, approved, applied, rejected (verified comes from a verification)"
        )
    user, proposal = _proposal(api, req, params["id"], ROLE_FOR_STATE[state])
    updated = proposals.set_state(
        api.app.store, proposal, cast(Any, state), by=f"user:{user.id}", note=str(body.get("note", ""))
    )
    details = {"state": state}
    api.app.auth.audit(
        user.id,
        "proposal_state_changed",
        environment_id=proposal.environment_id,
        subject=f"proposal:{proposal.id}",
        details=details,
    )
    return Response(200, {"proposal": dump(updated)})


@route("POST", "/api/proposals/(?P<id>[^/]+)/verify")
def verify_proposal(api: Api, req: Request, params: dict[str, str]) -> Response:
    user, proposal = _proposal(api, req, params["id"], "operator")
    env = api.app.store.get(Environment, proposal.environment_id)
    ctx = Context(environment=env, cluster=api.app.cluster_for(env), store=api.app.store, user=user)
    updated, after = proposals.verify(api.app.registry, api.app.auth, ctx, proposal)
    outcome = updated.verifications[-1]
    details = {"outcome": outcome.outcome, "after_evidence": len(after)}
    api.app.auth.audit(
        user.id,
        "proposal_verified",
        environment_id=env.id,
        subject=f"proposal:{proposal.id}",
        details=details,
    )
    return Response(200, {"proposal": dump(updated), "verification": dump(outcome)})
