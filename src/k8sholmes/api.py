"""The JSON API (design §15): every route checks the session, the role and the environment (§14.1).

Routes are plain functions on `Api` matched by method and path. Domain work goes to the engine, the
registry, the knowledge and findings modules; this module only parses, checks and shapes JSON.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from k8sholmes import findings as findings_mod
from k8sholmes import knowledge
from k8sholmes.app import App
from k8sholmes.capabilities import Context
from k8sholmes.environments import connect, create_environment, take_inventory
from k8sholmes.errors import (
    InvalidInput,
    InvestigationStateError,
    K8sHolmesError,
    NotFound,
    PermissionRefused,
)
from k8sholmes.models import OpenAICompatible, api_key_for, price_of, run_capability_tests, write_secret
from k8sholmes.records import (
    AcceptedException,
    AuditEntry,
    Call,
    CapabilitySettings,
    Environment,
    Evidence,
    Finding,
    Investigation,
    Knowledge,
    Message,
    ModelCall,
    ModelEndpoint,
    ModelLimits,
    ModelPrice,
    ResourceRef,
    Role,
    Scope,
    Step,
    User,
)
from k8sholmes.reports import investigation_json, report_text
from k8sholmes.runner import Runner

log = logging.getLogger("k8sholmes")
SESSION_COOKIE = "k8sholmes_session"


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    body: dict[str, Any] | None
    cookies: dict[str, str]
    user: User | None = None


@dataclass
class Response:
    status: int = 200
    body: Any = None
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict[str, str])
    stream: str | None = None  # an investigation id: the handler streams its events

    def bytes(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        if self.content_type == "application/json":
            return json.dumps(self.body, default=_json_default).encode()
        return str(self.body).encode()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


Handler = Callable[["Api", Request, dict[str, str]], Response]
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []
STATUS_FOR: dict[type[K8sHolmesError], int] = {
    NotFound: 404,
    InvalidInput: 400,
    PermissionRefused: 403,
    InvestigationStateError: 409,
}


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn

    return register


def _dump(record: Any) -> dict[str, Any]:
    return cast(dict[str, Any], record.model_dump(mode="json"))


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if len(text) == 10:
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Api:
    """The API over an `App` and the `Runner` that executes investigations in the background."""

    def __init__(self, app: App, runner: Runner) -> None:
        self.app = app
        self.runner = runner
        self.setup_token: str | None = None
        if app.auth.needs_setup():
            path = app.home / "setup-token"
            if path.exists():
                self.setup_token = path.read_text().strip() or None
            if not self.setup_token:
                self.setup_token = secrets.token_urlsafe(24)
                path.write_text(self.setup_token)
                path.chmod(0o600)

    # dispatch

    def handle(self, req: Request) -> Response:
        req.user = self.app.auth.user_for_token(req.cookies.get(SESSION_COOKIE))
        for method, pattern, handler in ROUTES:
            m = pattern.match(req.path)
            if m and method == req.method:
                return self._call(handler, req, m.groupdict())
        if any(pattern.match(req.path) for _, pattern, _ in ROUTES):
            return Response(405, {"error": "method not allowed"})
        return Response(404, {"error": "no such route"})

    def _call(self, handler: Handler, req: Request, params: dict[str, str]) -> Response:
        try:
            return handler(self, req, params)
        except K8sHolmesError as exc:
            status = STATUS_FOR.get(type(exc), 400)
            if isinstance(exc, PermissionRefused) and req.user is None:
                status = 401
            return Response(status, {"error": str(exc)})
        except Exception:
            log.exception("unhandled error on %s %s", req.method, req.path)
            return Response(500, {"error": "something went wrong; the server log has the details"})

    # checks

    def _user(self, req: Request) -> User:
        if req.user is None:
            raise PermissionRefused("sign in first")
        return req.user

    def _admin(self, req: Request) -> User:
        user = self._user(req)
        if not user.is_admin:
            raise PermissionRefused("this needs an administrator")
        return user

    def _env(self, req: Request, environment_id: str, role: Role) -> tuple[User, Environment]:
        user = self._user(req)
        env = self.app.store.get(Environment, environment_id)
        self.app.auth.require(user, env.id, role)
        return user, env

    def _visible_environments(self, user: User) -> list[Environment]:
        envs = self.app.store.find(Environment, order_by="name")
        return [e for e in envs if self.app.auth.role(user, e.id) is not None]

    def _body(self, req: Request) -> dict[str, Any]:
        if req.body is None:
            raise InvalidInput("a JSON body is required")
        return req.body

    # account

    @route("GET", "/api/me")
    def me(self, req: Request, params: dict[str, str]) -> Response:
        if req.user is None:
            return Response(
                200, {"user": None, "needs_setup": self.app.auth.needs_setup(), "environments": []}
            )
        envs = [
            {"id": e.id, "name": e.name, "default_model_id": e.settings.default_model_id}
            for e in self._visible_environments(req.user)
        ]
        user = _dump(req.user)
        user.pop("password_hash")
        return Response(200, {"user": user, "needs_setup": False, "environments": envs})

    @route("POST", "/api/setup")
    def setup(self, req: Request, params: dict[str, str]) -> Response:
        body = self._body(req)
        if not self.app.auth.needs_setup():
            raise PermissionRefused("this K8sHolmes is set up already")
        if not self.setup_token or not secrets.compare_digest(str(body.get("token", "")), self.setup_token):
            raise PermissionRefused("the setup token does not match the one the server printed")
        user = self.app.auth.create_user(
            str(body.get("username", "")), str(body.get("password", "")), is_admin=True
        )
        self.setup_token = None
        (self.app.home / "setup-token").unlink(missing_ok=True)
        self.app.auth.audit(user.id, "setup_completed")
        return Response(201, {"user": {"id": user.id, "username": user.username}})

    @route("POST", "/api/session")
    def sign_in(self, req: Request, params: dict[str, str]) -> Response:
        body = self._body(req)
        try:
            token = self.app.auth.sign_in(str(body.get("username", "")), str(body.get("password", "")))
        except PermissionRefused as exc:
            return Response(401, {"error": str(exc)})
        cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800"
        return Response(200, {"ok": True}, headers={"Set-Cookie": cookie})

    @route("DELETE", "/api/session")
    def sign_out(self, req: Request, params: dict[str, str]) -> Response:
        token = req.cookies.get(SESSION_COOKIE)
        if token:
            self.app.auth.sign_out(token)
        return Response(
            200, {"ok": True}, headers={"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0"}
        )

    # environments

    @route("GET", "/api/environments")
    def environments(self, req: Request, params: dict[str, str]) -> Response:
        user = self._user(req)
        return Response(200, {"environments": [_dump(e) for e in self._visible_environments(user)]})

    @route("POST", "/api/environments")
    def add_environment(self, req: Request, params: dict[str, str]) -> Response:
        user = self._admin(req)
        body = self._body(req)
        env = create_environment(
            self.app.store,
            name=str(body.get("name", "")),
            context=str(body.get("context", "")),
            kubeconfig=body.get("kubeconfig") or None,
        )
        self.app.auth.audit(user.id, "environment_created", environment_id=env.id)
        return Response(201, {"environment": _dump(env)})

    @route("POST", "/api/environments/(?P<id>[^/]+)/connect")
    def connect_environment(self, req: Request, params: dict[str, str]) -> Response:
        user, env = self._env(req, params["id"], "admin")
        env = connect(self.app.store, env, self.app.cluster_for(env))
        self.app.auth.audit(
            user.id,
            "environment_connected",
            environment_id=env.id,
            details={"server_version": env.server_version},
        )
        return Response(200, {"environment": _dump(env)})

    @route("POST", "/api/environments/(?P<id>[^/]+)/inventory")
    def inventory(self, req: Request, params: dict[str, str]) -> Response:
        _, env = self._env(req, params["id"], "investigator")
        inventory = take_inventory(self.app.store, env, self.app.cluster_for(env), [])
        return Response(
            200,
            {
                "inventory": {
                    "id": inventory.id,
                    "items": len(inventory.items),
                    "relationships": len(inventory.relationships),
                    "unreadable": inventory.unreadable,
                }
            },
        )

    @route("PATCH", "/api/environments/(?P<id>[^/]+)/settings")
    def environment_settings(self, req: Request, params: dict[str, str]) -> Response:
        user, env = self._env(req, params["id"], "admin")
        body = self._body(req)
        allowed = {"probes_enabled", "changes_authorized_by", "default_model_id", "retention"}
        changes = {k: v for k, v in body.items() if k in allowed}
        settings = env.settings.model_validate({**_dump(env.settings), **changes})
        env = env.model_copy(update={"settings": settings})
        self.app.store.put(env)
        self.app.auth.audit(user.id, "environment_settings_changed", environment_id=env.id, details=changes)
        return Response(200, {"environment": _dump(env)})

    @route("PATCH", "/api/environments/(?P<id>[^/]+)/capabilities/(?P<cap>[^/]+)")
    def capability_settings(self, req: Request, params: dict[str, str]) -> Response:
        user, env = self._env(req, params["id"], "operator")
        cap = self.app.registry.get(params["cap"])
        settings = CapabilitySettings.model_validate(self._body(req))
        env = env.model_copy(
            update={
                "settings": env.settings.model_copy(
                    update={"capabilities": {**env.settings.capabilities, cap.id: settings}}
                )
            }
        )
        self.app.store.put(env)
        self.app.auth.audit(
            user.id,
            "capability_settings_changed",
            environment_id=env.id,
            subject=f"capability:{cap.id}",
            details=_dump(settings),
        )
        return Response(200, {"settings": _dump(settings)})

    # capabilities

    @route("GET", "/api/capabilities")
    def capabilities(self, req: Request, params: dict[str, str]) -> Response:
        user = self._user(req)
        env = None
        if req.query.get("env"):
            _, env = self._env(req, req.query["env"], "viewer")
        rows: list[dict[str, Any]] = []
        for cap in self.app.registry.all():
            row = cap.describe()
            if env is not None:
                refusal = self.app.registry.refusal(cap, user, env, self.app.auth)
                row["available"] = refusal is None
                row["why_not"] = refusal.reason if refusal else None
                row["settings"] = _dump(self.app.registry.settings_for(cap, env))
            rows.append(row)
        return Response(200, {"capabilities": rows, "broken_extensions": self.app.registry.broken})

    @route("POST", "/api/capabilities/(?P<id>[^/]+)/test")
    def test_capability(self, req: Request, params: dict[str, str]) -> Response:
        body = self._body(req)
        user, env = self._env(req, str(body.get("environment_id", "")), "investigator")
        cap = self.app.registry.get(params["id"])
        ctx = Context(environment=env, cluster=self.app.cluster_for(env), store=self.app.store, user=user)
        call, evidence = self.app.registry.execute(
            cap.id, cast(dict[str, Any], body.get("inputs") or {}), ctx, self.app.auth
        )
        with self.app.store.transaction():
            self.app.store.put(call)
            if evidence is not None:
                self.app.store.put(evidence.model_copy(update={"retention": "short"}))
        self.app.auth.audit(
            user.id,
            "capability_tested",
            environment_id=env.id,
            subject=f"capability:{cap.id}",
            details={"status": call.status},
        )
        return Response(200, {"call": _dump(call), "evidence": _dump(evidence) if evidence else None})

    @route("GET", "/api/capabilities/(?P<id>[^/]+)/calls")
    def capability_calls(self, req: Request, params: dict[str, str]) -> Response:
        _, env = self._env(req, req.query.get("env", ""), "investigator")
        calls = self.app.store.find(Call, {"capability_id": params["id"]}, order_by="at DESC", limit=200)
        in_env = {i.id for i in self.app.store.find(Investigation, {"environment_id": env.id})}
        evidence_env = {
            e.call_id
            for e in self.app.store.find(Evidence, {"environment_id": env.id, "source": params["id"]})
        }
        rows = [c for c in calls if c.investigation_id in in_env or c.id in evidence_env][:50]
        return Response(200, {"calls": [_dump(c) for c in rows]})

    # investigations

    @route("POST", "/api/investigations")
    def start_investigation(self, req: Request, params: dict[str, str]) -> Response:
        body = self._body(req)
        user, env = self._env(req, str(body.get("environment_id", "")), "investigator")
        key = str(body.get("idempotency_key") or "")
        existing = self.runner.by_key(key) if key else None
        if existing:
            return Response(200, {"investigation": _dump(self.app.store.get(Investigation, existing))})
        scope = Scope(namespaces=[str(n) for n in cast(list[Any], body.get("namespaces") or [])])
        self.app.check_model(body.get("model_endpoint_id") or env.settings.default_model_id)
        inv = self.app.engine.start(
            environment=env,
            question=str(body.get("question", "")),
            user=user,
            scope=scope,
            model_endpoint_id=body.get("model_endpoint_id") or None,
        )
        self.runner.run(inv.id, user, key=key or None)
        return Response(201, {"investigation": _dump(inv)})

    @route("GET", "/api/investigations")
    def investigations(self, req: Request, params: dict[str, str]) -> Response:
        _, env = self._env(req, req.query.get("env", ""), "viewer")
        rows = self.app.store.find(
            Investigation, {"environment_id": env.id}, order_by="created_at DESC", limit=200
        )
        out = [
            {**_dump(i), "findings": self.app.store.count(Finding, {"investigation_id": i.id})} for i in rows
        ]
        return Response(200, {"investigations": out})

    def _investigation(
        self, req: Request, investigation_id: str, role: Role
    ) -> tuple[User, Investigation, Environment]:
        inv = self.app.store.get(Investigation, investigation_id)
        user, env = self._env(req, inv.environment_id, role)
        return user, inv, env

    @route("GET", "/api/investigations/(?P<id>[^/]+)")
    def investigation(self, req: Request, params: dict[str, str]) -> Response:
        _, inv, env = self._investigation(req, params["id"], "viewer")
        evidence = [
            {k: v for k, v in _dump(e).items() if k != "data"}
            for e in self.app.store.find(Evidence, {"investigation_id": inv.id}, order_by="collected_at")
        ]
        bundle = {
            "investigation": _dump(inv),
            "environment": {"id": env.id, "name": env.name},
            "steps": [
                _dump(s) for s in self.app.store.find(Step, {"investigation_id": inv.id}, order_by="seq")
            ],
            "calls": [
                _dump(c) for c in self.app.store.find(Call, {"investigation_id": inv.id}, order_by="at")
            ],
            "evidence": evidence,
            "findings": [
                _dump(f)
                for f in self.app.store.find(Finding, {"investigation_id": inv.id}, order_by="created_at")
            ],
            "messages": [
                _dump(m)
                for m in self.app.store.find(Message, {"investigation_id": inv.id}, order_by="seq")
                if m.role != "tool"
            ],
            "running": self.runner.is_running(inv.id),
        }
        return Response(200, bundle)

    @route("GET", "/api/investigations/(?P<id>[^/]+)/events")
    def investigation_events(self, req: Request, params: dict[str, str]) -> Response:
        _, inv, _ = self._investigation(req, params["id"], "viewer")
        return Response(200, None, content_type="text/event-stream", stream=inv.id)

    @route("POST", "/api/investigations/(?P<id>[^/]+)/cancel")
    def cancel(self, req: Request, params: dict[str, str]) -> Response:
        user, inv, _ = self._investigation(req, params["id"], "investigator")
        return Response(200, {"investigation": _dump(self.app.engine.cancel(inv.id, user))})

    @route("POST", "/api/investigations/(?P<id>[^/]+)/resume")
    def resume(self, req: Request, params: dict[str, str]) -> Response:
        user, inv, _ = self._investigation(req, params["id"], "investigator")
        if inv.status not in ("cancelled", "interrupted", "partial", "failed"):
            raise InvestigationStateError(f"investigation {inv.id} is {inv.status}")
        self.app.auth.audit(
            user.id,
            "investigation_resumed",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
        )
        self.runner.run(inv.id, user)
        return Response(202, {"investigation": _dump(self.app.store.get(Investigation, inv.id))})

    @route("POST", "/api/investigations/(?P<id>[^/]+)/follow_up")
    def follow_up(self, req: Request, params: dict[str, str]) -> Response:
        user, inv, _ = self._investigation(req, params["id"], "investigator")
        message = str(self._body(req).get("message", "")).strip()
        if not message:
            raise InvalidInput("the message is empty")
        self.runner.follow_up(inv.id, user, message)
        return Response(202, {"investigation": _dump(self.app.store.get(Investigation, inv.id))})

    @route("GET", "/api/investigations/(?P<id>[^/]+)/report")
    def report(self, req: Request, params: dict[str, str]) -> Response:
        _, inv, _ = self._investigation(req, params["id"], "viewer")
        return Response(200, report_text(self.app, inv), content_type="text/markdown; charset=utf-8")

    @route("GET", "/api/investigations/(?P<id>[^/]+)/export")
    def export(self, req: Request, params: dict[str, str]) -> Response:
        user, inv, _ = self._investigation(req, params["id"], "viewer")
        self.app.auth.audit(
            user.id, "export", environment_id=inv.environment_id, subject=f"investigation:{inv.id}"
        )
        payload = {"format_version": "1", **investigation_json(self.app, inv)}
        disposition = f'attachment; filename="{inv.id}.json"'
        return Response(200, payload, headers={"Content-Disposition": disposition})

    @route("GET", "/api/evidence/(?P<id>[^/]+)")
    def evidence(self, req: Request, params: dict[str, str]) -> Response:
        ev = self.app.store.get(Evidence, params["id"])
        self._env(req, ev.environment_id, "viewer")
        return Response(200, {"evidence": _dump(ev)})

    # findings

    @route("PATCH", "/api/findings/(?P<id>[^/]+)")
    def edit_finding(self, req: Request, params: dict[str, str]) -> Response:
        finding = self.app.store.get(Finding, params["id"])
        user, _ = self._env(req, finding.environment_id, "investigator")
        body = self._body(req)
        if body.get("state"):
            state = cast(Any, str(body["state"]))
            finding = findings_mod.set_state(
                self.app.store, finding, state, author=f"user:{user.id}", note=str(body.get("note", ""))
            )
            self.app.auth.audit(
                user.id,
                "finding_state_changed",
                environment_id=finding.environment_id,
                subject=f"finding:{finding.id}",
                details={"state": finding.state},
            )
        if body.get("edits"):
            finding = findings_mod.revise(
                self.app.store,
                finding,
                author=f"user:{user.id}",
                change="edited in the interface",
                **cast(dict[str, Any], body["edits"]),
            )
            self.app.auth.audit(
                user.id,
                "finding_edited",
                environment_id=finding.environment_id,
                subject=f"finding:{finding.id}",
            )
        return Response(200, {"finding": _dump(finding)})

    @route("POST", "/api/findings/(?P<id>[^/]+)/accept")
    def accept_finding(self, req: Request, params: dict[str, str]) -> Response:
        finding = self.app.store.get(Finding, params["id"])
        user, _ = self._env(req, finding.environment_id, "investigator")
        body = self._body(req)
        if len(finding.resources) != 1:
            raise InvalidInput("accept a finding on exactly one resource; this one names several or none")
        exc = knowledge.create_exception(
            self.app.store,
            AcceptedException(
                environment_id=finding.environment_id,
                condition=finding.condition,
                scope=finding.resources[0],
                explanation=str(body.get("explanation", "")),
                approved_by=f"user:{user.id}",
                review_at=_dt(body.get("review_at")),
                expires_at=_dt(body.get("expires_at")),
                reconsider_when=str(body.get("reconsider_when", "")),
                triggers=cast(Any, body.get("triggers") or []),
                finding_id=finding.id,
            ),
        )
        finding = findings_mod.set_state(
            self.app.store, finding, "accepted_risk", author=f"user:{user.id}", note=f"exception {exc.id}"
        )
        finding = finding.model_copy(update={"exception_id": exc.id})
        self.app.store.put(finding)
        self.app.auth.audit(
            user.id,
            "exception_created",
            environment_id=finding.environment_id,
            subject=f"exception:{exc.id}",
            details={"condition": exc.condition, "scope": _dump(exc.scope)},
        )
        return Response(201, {"exception": _dump(exc), "finding": _dump(finding)})

    # knowledge

    @route("GET", "/api/knowledge")
    def knowledge_list(self, req: Request, params: dict[str, str]) -> Response:
        _, env = self._env(req, req.query.get("env", ""), "viewer")
        notes = self.app.store.find(Knowledge, {"environment_id": env.id}, order_by="created_at DESC")
        exceptions = self.app.store.find(AcceptedException, {"environment_id": env.id})
        return Response(
            200, {"knowledge": [_dump(n) for n in notes], "exceptions": [_dump(e) for e in exceptions]}
        )

    @route("POST", "/api/knowledge")
    def add_knowledge(self, req: Request, params: dict[str, str]) -> Response:
        body = self._body(req)
        user, env = self._env(req, str(body.get("environment_id", "")), "investigator")
        scope = ResourceRef.model_validate(body["scope"]) if body.get("scope") else None
        note = knowledge.create_note(
            self.app.store,
            environment_id=env.id,
            kind=body.get("kind", "lesson"),
            text=str(body.get("text", "")),
            author=f"user:{user.id}",
            scope=scope,
        )
        self.app.auth.audit(user.id, "knowledge_added", environment_id=env.id, subject=f"knowledge:{note.id}")
        return Response(201, {"knowledge": _dump(note)})

    @route("PATCH", "/api/knowledge/(?P<id>[^/]+)")
    def edit_knowledge(self, req: Request, params: dict[str, str]) -> Response:
        note = self.app.store.get(Knowledge, params["id"])
        user, _ = self._env(req, note.environment_id, "investigator")
        note = knowledge.revise_note(
            self.app.store, note, f"user:{user.id}", str(self._body(req).get("text", ""))
        )
        return Response(200, {"knowledge": _dump(note)})

    @route("PATCH", "/api/exceptions/(?P<id>[^/]+)")
    def edit_exception(self, req: Request, params: dict[str, str]) -> Response:
        exc = self.app.store.get(AcceptedException, params["id"])
        user, _ = self._env(req, exc.environment_id, "investigator")
        body = self._body(req)
        updates: dict[str, Any] = {
            k: v for k, v in body.items() if k in ("explanation", "reconsider_when", "triggers", "applies_to")
        }
        for key in ("review_at", "expires_at"):
            if key in body:
                updates[key] = _dt(body[key])
        exc = knowledge.revise_exception(
            self.app.store, exc, f"user:{user.id}", "edited in the interface", **updates
        )
        self.app.auth.audit(
            user.id,
            "exception_edited",
            environment_id=exc.environment_id,
            subject=f"exception:{exc.id}",
            details={"fields": sorted(updates)},
        )
        return Response(200, {"exception": _dump(exc)})

    @route("POST", "/api/exceptions/(?P<id>[^/]+)/revoke")
    def revoke_exception(self, req: Request, params: dict[str, str]) -> Response:
        exc = self.app.store.get(AcceptedException, params["id"])
        user, _ = self._env(req, exc.environment_id, "investigator")
        exc = knowledge.revoke_exception(
            self.app.store, exc, f"user:{user.id}", str(self._body(req).get("reason", ""))
        )
        self.app.auth.audit(
            user.id,
            "exception_revoked",
            environment_id=exc.environment_id,
            subject=f"exception:{exc.id}",
            details={"reason": exc.revocation_reason},
        )
        return Response(200, {"exception": _dump(exc)})

    # models

    def _endpoint_row(self, endpoint: ModelEndpoint) -> dict[str, Any]:
        calls = self.app.store.find(ModelCall, {"endpoint_id": endpoint.id})
        prompt, completion = sum(c.prompt_tokens for c in calls), sum(c.completion_tokens for c in calls)
        row = _dump(endpoint)
        row["has_key"] = api_key_for(endpoint, self.app.home) is not None
        row["usage"] = {
            "calls": len(calls),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "price": price_of(endpoint, prompt, completion),
        }
        return row

    @route("GET", "/api/models")
    def models(self, req: Request, params: dict[str, str]) -> Response:
        self._user(req)
        return Response(
            200,
            {
                "endpoints": [
                    self._endpoint_row(e) for e in self.app.store.find(ModelEndpoint, order_by="name")
                ]
            },
        )

    @route("POST", "/api/models")
    def add_model(self, req: Request, params: dict[str, str]) -> Response:
        user = self._admin(req)
        body = self._body(req)
        endpoint = ModelEndpoint(
            name=str(body.get("name", "")).strip() or "endpoint",
            base_url=str(body.get("base_url", "")).strip(),
            model=str(body.get("model", "")).strip(),
            api_key_env=body.get("api_key_env") or None,
            limits=ModelLimits.model_validate(body.get("limits") or {}),
            price=ModelPrice.model_validate(body["price"]) if body.get("price") else None,
            is_default=not self.app.store.find(ModelEndpoint, {"is_default": True}),
        )
        if not endpoint.base_url.startswith(("http://", "https://")) or not endpoint.model:
            raise InvalidInput("a model endpoint needs an http(s) base URL and a model name")
        self.app.store.put(endpoint)
        if body.get("api_key"):
            write_secret(self.app.home, endpoint.id, str(body["api_key"]))
        self.app.auth.audit(
            user.id,
            "model_endpoint_added",
            subject=f"model:{endpoint.id}",
            details={"base_url": endpoint.base_url, "model": endpoint.model},
        )
        return Response(201, {"endpoint": self._endpoint_row(endpoint)})

    @route("PATCH", "/api/models/(?P<id>[^/]+)")
    def edit_model(self, req: Request, params: dict[str, str]) -> Response:
        user = self._admin(req)
        endpoint = self.app.store.get(ModelEndpoint, params["id"])
        body = self._body(req)
        if body.get("is_default"):
            for other in self.app.store.find(ModelEndpoint, {"is_default": True}):
                self.app.store.put(other.model_copy(update={"is_default": False}))
            endpoint = endpoint.model_copy(update={"is_default": True})
        if "limits" in body:
            endpoint = endpoint.model_copy(update={"limits": ModelLimits.model_validate(body["limits"])})
        self.app.store.put(endpoint)
        self.app.auth.audit(user.id, "model_endpoint_changed", subject=f"model:{endpoint.id}")
        return Response(200, {"endpoint": self._endpoint_row(endpoint)})

    @route("POST", "/api/models/(?P<id>[^/]+)/test")
    def test_model(self, req: Request, params: dict[str, str]) -> Response:
        user = self._admin(req)
        endpoint = self.app.store.get(ModelEndpoint, params["id"])
        if endpoint.base_url.startswith("demo://"):
            raise InvalidInput("the demo investigator is not a model endpoint; it needs no tests")
        results = run_capability_tests(OpenAICompatible(endpoint, api_key_for(endpoint, self.app.home)))
        endpoint = endpoint.model_copy(update={"tests": results})
        self.app.store.put(endpoint)
        self.app.auth.audit(
            user.id,
            "model_endpoint_tested",
            subject=f"model:{endpoint.id}",
            details={k: v.status for k, v in results.items()},
        )
        return Response(
            200,
            {"endpoint": self._endpoint_row(endpoint), "tests": {k: _dump(v) for k, v in results.items()}},
        )

    # users and audit

    @route("GET", "/api/users")
    def users(self, req: Request, params: dict[str, str]) -> Response:
        self._admin(req)
        rows = [
            {k: v for k, v in _dump(u).items() if k != "password_hash"}
            for u in self.app.store.find(User, order_by="username")
        ]
        return Response(200, {"users": rows})

    @route("POST", "/api/users")
    def add_user(self, req: Request, params: dict[str, str]) -> Response:
        admin = self._admin(req)
        body = self._body(req)
        user = self.app.auth.create_user(
            str(body.get("username", "")), str(body.get("password", "")), is_admin=bool(body.get("is_admin"))
        )
        self.app.auth.audit(admin.id, "user_created", subject=f"user:{user.id}")
        return Response(201, {"user": {"id": user.id, "username": user.username, "is_admin": user.is_admin}})

    @route("PUT", "/api/users/(?P<id>[^/]+)/grants")
    def set_grant(self, req: Request, params: dict[str, str]) -> Response:
        admin = self._admin(req)
        body = self._body(req)
        user = self.app.store.get(User, params["id"])
        env = self.app.store.get(Environment, str(body.get("environment_id", "")))
        role = body.get("role") or None
        if role not in (None, "viewer", "investigator", "operator"):
            raise InvalidInput("the role is viewer, investigator, operator or empty to remove access")
        user = self.app.auth.set_grant(user, env.id, cast(Any, role))
        self.app.auth.audit(
            admin.id,
            "grant_changed",
            environment_id=env.id,
            subject=f"user:{user.id}",
            details={"role": role},
        )
        return Response(200, {"user": {"id": user.id, "grants": [_dump(g) for g in user.grants]}})

    @route("GET", "/api/audit")
    def audit(self, req: Request, params: dict[str, str]) -> Response:
        self._admin(req)
        where = {"environment_id": req.query["env"]} if req.query.get("env") else None
        entries = self.app.store.find(AuditEntry, where, order_by="at DESC", limit=200)
        names = {u.id: u.username for u in self.app.store.find(User)}
        return Response(
            200, {"entries": [{**_dump(a), "user": names.get(a.user_id or "", a.user_id)} for a in entries]}
        )


def static_file(page_dir: Path, path: str) -> tuple[bytes, str] | None:
    """A file of the interface, or None. Paths never leave the page folder."""
    name = path.lstrip("/") or "index.html"
    target = (page_dir / name).resolve()
    if not str(target).startswith(str(page_dir.resolve())) or not target.is_file():
        return None
    types = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }
    return target.read_bytes(), types.get(target.suffix, "application/octet-stream")
