"""Users, roles, sessions and the audit log (design §14.1, §14.4).

Local accounts: a username and an `scrypt` hash. Sessions are random tokens; the store keeps only their
hash. Roles are per environment; an admin has every role everywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from k8sholmes.errors import InvalidInput, NotFound, PermissionRefused
from k8sholmes.records import ROLE_ORDER, AuditEntry, Grant, Role, Session, User, now
from k8sholmes.store import Store

SESSION_LIFETIME = timedelta(days=7)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise InvalidInput("the password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    """Accounts, sign-in and the permission checks every API route and the capability gate use."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # accounts

    def needs_setup(self) -> bool:
        return self.store.count(User) == 0

    def create_user(
        self, username: str, password: str, *, is_admin: bool = False, grants: list[Grant] | None = None
    ) -> User:
        username = username.strip()
        if not username:
            raise InvalidInput("the username is empty")
        if self.store.find(User, {"username": username}):
            raise InvalidInput(f"the username {username!r} is taken")
        user = User(
            username=username, password_hash=hash_password(password), is_admin=is_admin, grants=grants or []
        )
        self.store.put(user)
        return user

    def user(self, user_id: str) -> User:
        return self.store.get(User, user_id)

    def set_grant(self, user: User, environment_id: str, role: Role | None) -> User:
        grants = [g for g in user.grants if g.environment_id != environment_id]
        if role is not None:
            grants.append(Grant(environment_id=environment_id, role=role))
        user = user.model_copy(update={"grants": grants})
        self.store.put(user)
        return user

    # sessions

    def sign_in(self, username: str, password: str) -> str:
        users = self.store.find(User, {"username": username})
        if not users or not verify_password(password, users[0].password_hash):
            self.audit(None, "sign_in_failed", details={"username": username})
            raise PermissionRefused("wrong username or password")
        token = secrets.token_urlsafe(32)
        self.store.put(
            Session(token_hash=_token_hash(token), user_id=users[0].id, expires_at=now() + SESSION_LIFETIME)
        )
        self.audit(users[0].id, "sign_in")
        return token

    def user_for_token(self, token: str | None) -> User | None:
        if not token:
            return None
        sessions = self.store.find(Session, {"token_hash": _token_hash(token)})
        if not sessions:
            return None
        session = sessions[0]
        if session.expires_at <= now():
            self.store.delete(Session, session.id)
            return None
        try:
            return self.store.get(User, session.user_id)
        except NotFound:
            return None

    def sign_out(self, token: str) -> None:
        for session in self.store.find(Session, {"token_hash": _token_hash(token)}):
            self.store.delete(Session, session.id)

    # roles

    @staticmethod
    def role(user: User, environment_id: str) -> Role | None:
        if user.is_admin:
            return "admin"
        for grant in user.grants:
            if grant.environment_id == environment_id:
                return grant.role
        return None

    def has_role(self, user: User, environment_id: str, needed: Role) -> bool:
        role = self.role(user, environment_id)
        return role is not None and ROLE_ORDER[role] >= ROLE_ORDER[needed]

    def require(self, user: User, environment_id: str, needed: Role) -> None:
        """Raise `PermissionRefused` unless the user has at least `needed` in the environment."""
        if not self.has_role(user, environment_id, needed):
            raise PermissionRefused(
                f"this needs the {needed} role in environment {environment_id}; "
                f"{user.username} has {self.role(user, environment_id) or 'no access'}"
            )

    # audit

    def audit(
        self,
        user_id: str | None,
        action: str,
        *,
        environment_id: str | None = None,
        subject: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            user_id=user_id,
            action=action,
            environment_id=environment_id,
            subject=subject,
            details=details or {},
        )
        self.store.put(entry)
        return entry
