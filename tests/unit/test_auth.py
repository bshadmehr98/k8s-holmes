from datetime import timedelta
from pathlib import Path

import pytest

from k8sholmes import records
from k8sholmes.auth import Auth, hash_password, verify_password
from k8sholmes.errors import InvalidInput, PermissionRefused
from k8sholmes.store import Store


@pytest.fixture
def auth(tmp_path: Path) -> Auth:
    return Auth(Store(tmp_path / "db"))


def test_passwords_are_hashed_with_scrypt_and_verified() -> None:
    h = hash_password("correct horse")
    assert h.startswith("scrypt$")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    with pytest.raises(InvalidInput):
        hash_password("short")


def test_setup_sign_in_and_sessions(auth: Auth) -> None:
    assert auth.needs_setup()
    admin = auth.create_user("root", "a strong one", is_admin=True)
    assert not auth.needs_setup()
    token = auth.sign_in("root", "a strong one")
    assert auth.user_for_token(token) == admin
    assert auth.user_for_token("nope") is None
    assert auth.user_for_token(None) is None
    auth.sign_out(token)
    assert auth.user_for_token(token) is None
    with pytest.raises(PermissionRefused):
        auth.sign_in("root", "wrong")
    actions = [a.action for a in auth.store.find(records.AuditEntry)]
    assert actions == ["sign_in", "sign_in_failed"]


def test_expired_sessions_are_dropped(auth: Auth) -> None:
    auth.create_user("u", "a strong one")
    token = auth.sign_in("u", "a strong one")
    session = auth.store.find(records.Session)[0]
    auth.store.put(session.model_copy(update={"expires_at": records.now() - timedelta(seconds=1)}))
    assert auth.user_for_token(token) is None
    assert auth.store.count(records.Session) == 0


def test_duplicate_or_empty_usernames_are_refused(auth: Auth) -> None:
    auth.create_user("u", "a strong one")
    with pytest.raises(InvalidInput, match="taken"):
        auth.create_user("u", "a strong one")
    with pytest.raises(InvalidInput, match="empty"):
        auth.create_user("  ", "a strong one")


def test_roles_are_per_environment_and_admin_is_everything(auth: Auth) -> None:
    viewer = auth.create_user(
        "v", "a strong one", grants=[records.Grant(environment_id="env_a", role="viewer")]
    )
    admin = auth.create_user("a", "a strong one", is_admin=True)
    assert auth.role(viewer, "env_a") == "viewer"
    assert auth.role(viewer, "env_b") is None
    assert auth.has_role(viewer, "env_a", "viewer")
    assert not auth.has_role(viewer, "env_a", "investigator")
    assert auth.role(admin, "anything") == "admin"
    with pytest.raises(PermissionRefused, match="needs the investigator role"):
        auth.require(viewer, "env_a", "investigator")
    with pytest.raises(PermissionRefused, match="no access"):
        auth.require(viewer, "env_b", "viewer")
    viewer = auth.set_grant(viewer, "env_a", "operator")
    assert auth.has_role(viewer, "env_a", "investigator")
    viewer = auth.set_grant(viewer, "env_a", None)
    assert auth.role(viewer, "env_a") is None


def test_audit_entries_carry_subject_and_details(auth: Auth) -> None:
    entry = auth.audit(
        "usr_1", "exception_created", environment_id="env_a", subject="exception:exc_1", details={"k": 1}
    )
    assert auth.store.get(records.AuditEntry, entry.id).details == {"k": 1}
