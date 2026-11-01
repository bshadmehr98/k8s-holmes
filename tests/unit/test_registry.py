from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Capability, Context, Registry
from k8sholmes.errors import InvalidInput, NotFound, PermissionRefused
from k8sholmes.evidence import Result
from k8sholmes.records import CapabilityClass
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster


class EchoInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    times: int = 1


RAN: list[BaseModel] = []


def echo(inputs: BaseModel, ctx: Context) -> Result:
    assert isinstance(inputs, EchoInputs)
    RAN.append(inputs)
    return Result(
        summary="echoed",
        data={"text": inputs.text * inputs.times, "note": "token=abcdefgh"},
        coverage="observed",
    )


def failing(inputs: BaseModel, ctx: Context) -> Result:
    raise NotFound('pods "ghost" not found')


def cap(
    id: str,
    class_: CapabilityClass = "inspect",
    run: Any = echo,
    permissions: tuple[tuple[str, str], ...] = (("get", "pods"),),
) -> Capability:
    return Capability(
        id=id,
        title=id,
        purpose=f"the {id} capability",
        class_=class_,
        systems=("kubernetes",),
        inputs=EchoInputs,
        permissions=permissions,
        run=run,
    )


@pytest.fixture
def world(
    tmp_path: Path,
) -> tuple[Registry, Auth, records.Environment, records.User, records.User, records.User]:
    store = Store(tmp_path / "db")
    auth = Auth(store)
    env = records.Environment(
        name="dev", context="kind-dev", permissions={"get pods": True, "list nodes": False}
    )
    store.put(env)
    viewer = auth.create_user(
        "v", "a strong one", grants=[records.Grant(environment_id=env.id, role="viewer")]
    )
    investigator = auth.create_user(
        "i", "a strong one", grants=[records.Grant(environment_id=env.id, role="investigator")]
    )
    operator = auth.create_user(
        "o", "a strong one", grants=[records.Grant(environment_id=env.id, role="operator")]
    )
    registry = Registry(
        [
            cap("test.echo"),
            cap("test.probe", "probe"),
            cap("test.change", "change"),
            cap("test.fail", run=failing),
            cap("test.nodes", permissions=(("list", "nodes"),)),
        ]
    )
    return registry, auth, env, viewer, investigator, operator


def ctx(store: Store, env: records.Environment, user: records.User) -> Context:
    return Context(environment=env, cluster=FakeCluster(), store=store, user=user, investigation_id="inv_1")


def test_registry_lookup_search_and_duplicates(world: Any) -> None:
    registry, *_ = world
    assert [c.id for c in registry.all()][:2] == ["test.change", "test.echo"]
    with pytest.raises(NotFound, match="does not exist"):
        registry.get("test.missing")
    with pytest.raises(InvalidInput, match="twice"):
        registry.add(cap("test.echo"))
    assert registry.get("test.echo").describe()["inputs"]["properties"]["text"]["type"] == "string"


def test_validation_names_the_field(world: Any) -> None:
    registry, *_ = world
    with pytest.raises(InvalidInput, match="times: Input should be a valid integer"):
        registry.validate(registry.get("test.echo"), {"text": "a", "times": "x"})
    with pytest.raises(InvalidInput, match="extra: Extra inputs are not permitted"):
        registry.validate(registry.get("test.echo"), {"text": "a", "extra": 1})
    with pytest.raises(InvalidInput, match="text: Field required"):
        registry.validate(registry.get("test.echo"), {})


def test_gate_by_role_class_settings_and_permissions(world: Any) -> None:
    registry, auth, env, viewer, investigator, operator = world
    assert registry.refusal(registry.get("test.echo"), investigator, env, auth) is None
    assert registry.refusal(registry.get("test.echo"), viewer, env, auth).kind == "role"
    assert registry.refusal(registry.get("test.probe"), operator, env, auth).kind == "class"
    probes_on = env.model_copy(update={"settings": env.settings.model_copy(update={"probes_enabled": True})})
    assert registry.refusal(registry.get("test.probe"), operator, probes_on, auth) is None
    assert registry.refusal(registry.get("test.probe"), investigator, probes_on, auth).kind == "role"
    change = registry.refusal(registry.get("test.change"), operator, env, auth)
    assert change.kind == "class"
    assert "authorization path" in change.reason
    authorized = env.model_copy(
        update={"settings": env.settings.model_copy(update={"changes_authorized_by": "operator_role"})}
    )
    assert registry.refusal(registry.get("test.change"), operator, authorized, auth) is None
    assert registry.refusal(registry.get("test.change"), investigator, authorized, auth).kind == "role"
    disabled = env.model_copy(
        update={
            "settings": env.settings.model_copy(
                update={"capabilities": {"test.echo": records.CapabilitySettings(enabled=False)}}
            )
        }
    )
    assert registry.refusal(registry.get("test.echo"), investigator, disabled, auth).kind == "disabled"
    missing = registry.refusal(registry.get("test.nodes"), investigator, env, auth)
    assert missing.kind == "permissions"
    assert "list nodes" in missing.reason
    with pytest.raises(PermissionRefused):
        registry.permitted(registry.get("test.echo"), viewer, env, auth)


def test_execute_records_a_call_and_redacted_evidence(world: Any) -> None:
    registry, auth, env, _, investigator, _ = world
    call, evidence = registry.execute(
        "test.echo", {"text": "hi", "times": 2}, ctx(auth.store, env, investigator), auth, step_id="stp_1"
    )
    assert call.status == "completed"
    assert evidence is not None
    assert call.evidence_id == evidence.id
    assert evidence.data["text"] == "hihi"
    assert "abcdefgh" not in str(evidence.data)
    assert evidence.query == {"text": "hi", "times": 2}
    assert evidence.coverage == "observed"
    assert evidence.step_id == "stp_1"
    assert evidence.investigation_id == "inv_1"


def test_execute_refuses_and_audits_without_running(world: Any) -> None:
    registry, auth, env, viewer, _, _ = world
    RAN.clear()
    call, evidence = registry.execute("test.echo", {"text": "hi"}, ctx(auth.store, env, viewer), auth)
    assert (call.status, evidence) == ("refused", None)
    assert "investigator role" in call.reason
    assert RAN == []
    assert [a.action for a in auth.store.find(records.AuditEntry)] == ["call_refused"]


def test_execute_marks_invalid_inputs_and_unknown_capabilities(world: Any) -> None:
    registry, auth, env, _, investigator, _ = world
    RAN.clear()
    call, evidence = registry.execute("test.echo", {"nope": 1}, ctx(auth.store, env, investigator), auth)
    assert call.status == "invalid"
    assert RAN == []
    assert "text: Field required" in call.reason
    assert evidence is None
    call, _ = registry.execute("test.unknown", {}, ctx(auth.store, env, investigator), auth)
    assert call.status == "invalid"


def test_execute_keeps_a_failed_call_as_evidence(world: Any) -> None:
    registry, auth, env, _, investigator, _ = world
    call, evidence = registry.execute("test.fail", {"text": "x"}, ctx(auth.store, env, investigator), auth)
    assert call.status == "failed"
    assert evidence is not None
    assert evidence.error is not None
    assert evidence.coverage == "no_data"
    assert "ghost" in evidence.coverage_reason


def test_namespace_allow_list_and_per_capability_settings(world: Any) -> None:
    registry, auth, env, _, investigator, _ = world
    settings = {"test.scoped": records.CapabilitySettings(namespaces=["shop"], max_log_bytes=64)}
    limited = env.model_copy(update={"settings": env.settings.model_copy(update={"capabilities": settings})})

    class Scoped(BaseModel):
        model_config = ConfigDict(extra="forbid")
        namespace: str | None = None

    seen: list[int] = []

    def run(inputs: BaseModel, ctx: Context) -> Result:
        seen.append(ctx.settings.max_log_bytes)
        return Result(summary="ok", data={"x": "y" * 500}, coverage="observed")

    registry.add(
        Capability(
            id="test.scoped",
            title="t",
            purpose="scoped",
            class_="inspect",
            systems=("x",),
            inputs=Scoped,
            permissions=(),
            run=run,
        )
    )
    call, _ = registry.execute(
        "test.scoped", {"namespace": "other"}, ctx(auth.store, limited, investigator), auth
    )
    assert call.status == "refused"
    assert "limited to shop" in call.reason
    call, _ = registry.execute("test.scoped", {}, ctx(auth.store, limited, investigator), auth)
    assert call.status == "refused"  # every namespace is more than the allow-list
    call, evidence = registry.execute(
        "test.scoped", {"namespace": "shop"}, ctx(auth.store, limited, investigator), auth
    )
    assert call.status == "completed"
    assert seen == [64]  # this capability's own settings reached its run
    assert evidence is not None
    assert evidence.truncated  # and its own cap applied to the evidence


def test_any_exception_in_a_capability_is_recorded(world: Any) -> None:
    registry, auth, env, _, investigator, _ = world

    def boom(inputs: BaseModel, ctx: Context) -> Result:
        raise KeyError("unexpected shape")

    registry.add(
        Capability(
            id="test.boom",
            title="t",
            purpose="boom",
            class_="inspect",
            systems=("x",),
            inputs=EchoInputs,
            permissions=(),
            run=boom,
        )
    )
    call, evidence = registry.execute("test.boom", {"text": "x"}, ctx(auth.store, env, investigator), auth)
    assert call.status == "failed"
    assert evidence is not None
    assert evidence.error is not None
    assert evidence.error.startswith("KeyError")
    assert evidence.coverage == "no_data"
