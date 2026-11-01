"""The JSON API (design §15): every route checks the session, the role and the environment (§14.1).

Routes are plain functions registered with `@route` in the modules of this package, one per resource:
`account`, `environments` (and capabilities), `investigations` (and evidence, findings), `knowledge`,
`reports` (and shares, the live summary, the model comparison), `proposals`, `saved`, `settings` (models,
users, audit). This module holds the request and response shapes, the dispatch and the
checks every route uses. Domain work goes to the engine, the registry, the knowledge and findings modules.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError

from k8sholmes.app import App
from k8sholmes.auth import setup_token
from k8sholmes.errors import (
    InvalidInput,
    InvestigationStateError,
    K8sHolmesError,
    NotFound,
    PermissionRefused,
)
from k8sholmes.records import Environment, Role, User
from k8sholmes.runner import Runner

log = logging.getLogger("k8sholmes")
SESSION_COOKIE = "k8sholmes_session"
STATUS_FOR: dict[type[K8sHolmesError], int] = {
    NotFound: 404,
    InvalidInput: 400,
    PermissionRefused: 403,
    InvestigationStateError: 409,
}
PUBLIC_ROUTES = {("GET", "/api/me"), ("POST", "/api/setup"), ("POST", "/api/session")}


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
    stream: str | None = None  # an investigation id: the server streams its events

    def bytes(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        if self.content_type == "application/json":
            return json.dumps(self.body, default=json_default).encode()
        return str(self.body).encode()


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def dump(record: Any) -> dict[str, Any]:
    return cast(dict[str, Any], record.model_dump(mode="json"))


def parse_when(value: Any) -> datetime | None:
    """An ISO datetime, or a bare date from an HTML date input, as an aware UTC datetime."""
    if not value:
        return None
    text = str(value)
    if len(text) == len("2026-11-01"):
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


Handler = Callable[["Api", Request, dict[str, str]], Response]
ROUTES: list[tuple[str, re.Pattern[str], str, Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    """Register a route; patterns are anchored regular expressions with named groups."""

    def register(fn: Handler) -> Handler:
        ROUTES.append((method, re.compile("^" + pattern + "$"), pattern, fn))
        return fn

    return register


class Api:
    """The API over an `App` and the `Runner` that executes investigations in the background."""

    def __init__(self, app: App, runner: Runner) -> None:
        self.app = app
        self.runner = runner
        self.setup_token: str | None = setup_token(app.home) if app.auth.needs_setup() else None

    # dispatch

    def handle(self, req: Request) -> Response:
        req.user = self.app.auth.user_for_token(req.cookies.get(SESSION_COOKIE))
        for method, pattern, raw, handler in ROUTES:
            m = pattern.match(req.path)
            if m and method == req.method:
                if req.user is None and (method, raw) not in PUBLIC_ROUTES:
                    return Response(401, {"error": "sign in first"})
                return self._call(handler, req, m.groupdict())
        if any(pattern.match(req.path) for _, pattern, _, _ in ROUTES):
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
        except (ValidationError, ValueError) as exc:
            return Response(400, {"error": f"invalid input: {str(exc)[:500]}"})
        except Exception:
            log.exception("unhandled error on %s %s", req.method, req.path)
            return Response(500, {"error": "something went wrong; the server log has the details"})

    # checks every route uses

    def user(self, req: Request) -> User:
        if req.user is None:
            raise PermissionRefused("sign in first")
        return req.user

    def admin(self, req: Request) -> User:
        user = self.user(req)
        if not user.is_admin:
            raise PermissionRefused("this needs an administrator")
        return user

    def env(self, req: Request, environment_id: str, role: Role) -> tuple[User, Environment]:
        """The user and the environment, after the role check (design §14.1)."""
        user = self.user(req)
        env = self.app.store.get(Environment, environment_id)
        self.app.auth.require(user, env.id, role)
        return user, env

    def visible_environments(self, user: User) -> list[Environment]:
        envs = self.app.store.find(Environment, order_by="name")
        return [e for e in envs if self.app.auth.role(user, e.id) is not None]

    @staticmethod
    def body(req: Request) -> dict[str, Any]:
        if req.body is None:
            raise InvalidInput("a JSON body is required")
        return req.body


from k8sholmes.api import (  # noqa: E402
    account,
    environments,
    investigations,
    knowledge,
    proposals,
    reports,
    saved,
    settings,
)

_ROUTE_MODULES = (
    account,
    environments,
    investigations,
    knowledge,
    proposals,
    reports,
    saved,
    settings,
)  # imported for their @route registrations

__all__ = ["PUBLIC_ROUTES", "Api", "Request", "Response", "dump", "parse_when", "route"]
