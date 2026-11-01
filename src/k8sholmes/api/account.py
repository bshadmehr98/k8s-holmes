"""Account routes: who am I, the one-time setup, sign in and out (design §14.1)."""

from __future__ import annotations

import secrets

from k8sholmes.api import SESSION_COOKIE, Api, Request, Response, dump, route
from k8sholmes.errors import PermissionRefused


@route("GET", "/api/me")
def me(api: Api, req: Request, params: dict[str, str]) -> Response:
    if req.user is None:
        return Response(200, {"user": None, "needs_setup": api.app.auth.needs_setup(), "environments": []})
    envs = [
        {"id": e.id, "name": e.name, "default_model_id": e.settings.default_model_id}
        for e in api.visible_environments(req.user)
    ]
    user = dump(req.user)
    user.pop("password_hash")
    return Response(200, {"user": user, "needs_setup": False, "environments": envs})


@route("POST", "/api/setup")
def setup(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    if not api.app.auth.needs_setup():
        raise PermissionRefused("this K8sHolmes is set up already")
    if not api.setup_token or not secrets.compare_digest(str(body.get("token", "")), api.setup_token):
        raise PermissionRefused("the setup token does not match the one the server printed")
    user = api.app.auth.create_user(
        str(body.get("username", "")), str(body.get("password", "")), is_admin=True
    )
    api.setup_token = None
    (api.app.home / "setup-token").unlink(missing_ok=True)
    api.app.auth.audit(user.id, "setup_completed")
    return Response(201, {"user": {"id": user.id, "username": user.username}})


@route("POST", "/api/session")
def sign_in(api: Api, req: Request, params: dict[str, str]) -> Response:
    body = api.body(req)
    try:
        token = api.app.auth.sign_in(str(body.get("username", "")), str(body.get("password", "")))
    except PermissionRefused as exc:
        return Response(401, {"error": str(exc)})
    cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800"
    return Response(200, {"ok": True}, headers={"Set-Cookie": cookie})


@route("DELETE", "/api/session")
def sign_out(api: Api, req: Request, params: dict[str, str]) -> Response:
    token = req.cookies.get(SESSION_COOKIE)
    if token:
        api.app.auth.sign_out(token)
    return Response(
        200, {"ok": True}, headers={"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0"}
    )
