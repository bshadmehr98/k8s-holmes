"""Guarantees 1, 2, 19 and 23: the gate, input validation, the change path, and data that is not instructions."""

from k8sholmes.records import AuditEntry, Call, Grant
from k8sholmes.testing.demo import PLANTED_INSTRUCTION
from k8sholmes.testing.model import calls, structured, text
from tests.e2e.conftest import Demo

EMPTY = {
    "answer": "nothing to say",
    "findings": [],
    "limitations": [],
    "next_steps": [],
    "knowledge_used": [],
}
FRAME = {
    "question": "q",
    "namespaces": ["shop"],
    "resources": [],
    "period_hours": 24,
    "hypotheses": [],
    "intended_capabilities": [],
}


def scripted(demo: Demo, tool_calls):
    from k8sholmes.testing import ScriptedModel

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            return structured(EMPTY)
        if not any(m.get("role") == "tool" for m in messages):
            return calls(*tool_calls)
        return text("done")

    model = ScriptedModel(script=script)
    demo.app.engine.model_for = lambda inv: model
    return model


def test_a_probe_a_change_and_an_invalid_call_are_refused_recorded_and_make_it_partial(demo: Demo) -> None:
    scripted(
        demo,
        [
            ("http.probe_url", {"url": "http://example.invalid/"}),
            ("k8s.list", {"kind": "pods", "bogus": True}),
        ],
    )
    inv = demo.ask("probe things")
    by_cap = {c.capability_id: c for c in demo.app.store.find(Call, {"investigation_id": inv.id})}
    assert by_cap["http.probe_url"].status == "refused"
    assert "probes are not enabled" in by_cap["http.probe_url"].reason
    assert by_cap["k8s.list"].status == "invalid"
    assert "bogus: Extra inputs are not permitted" in by_cap["k8s.list"].reason
    assert inv.status == "partial"
    assert any(lim.startswith("refused:") for lim in inv.limitations)
    assert any(a.action == "call_refused" for a in demo.app.store.find(AuditEntry))


def test_a_viewer_cannot_investigate(demo: Demo) -> None:
    import pytest

    from k8sholmes.errors import PermissionRefused

    viewer = demo.app.auth.create_user(
        "viewer", "a strong one", grants=[Grant(environment_id=demo.env.id, role="viewer")]
    )
    with pytest.raises(PermissionRefused):
        demo.app.engine.start(environment=demo.env, question="x", user=viewer)


def test_an_instruction_inside_a_log_runs_nothing(demo: Demo) -> None:
    from k8sholmes.testing import ScriptedModel

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            return structured(EMPTY)
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            return calls(
                ("k8s.logs", {"pod": "legacy-export-77aa-tt4w2", "namespace": "shop", "container": "export"})
            )
        if len(tool_messages) == 1:
            assert PLANTED_INSTRUCTION in tool_messages[0]["content"]
            return calls(
                ("k8s.delete_namespace", {"namespace": "shop"}),
                ("http.probe_url", {"url": "http://evil.invalid/"}),
            )
        return text("done")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=script)
    inv = demo.ask("read the legacy logs")
    statuses = {c.capability_id: c.status for c in demo.app.store.find(Call, {"investigation_id": inv.id})}
    assert statuses["k8s.delete_namespace"] == "invalid"
    assert statuses["http.probe_url"] == "refused"
    from typing import cast

    from k8sholmes.testing import FakeCluster

    cluster = cast(FakeCluster, demo.app.cluster_for(demo.env))
    assert all(
        c[0] in ("get", "list", "events", "logs", "top", "can_i", "version", "api_resources")
        for c in cluster.calls
    )


def test_no_change_capability_ships_and_the_class_needs_a_path(demo: Demo) -> None:
    assert all(c.class_ != "change" for c in demo.app.registry.all())
    from pydantic import BaseModel, ConfigDict

    from k8sholmes.capabilities import Capability
    from k8sholmes.evidence import Result

    class NoInputs(BaseModel):
        model_config = ConfigDict(extra="forbid")

    change = Capability(
        id="test.change",
        title="t",
        purpose="p",
        class_="change",
        systems=("x",),
        inputs=NoInputs,
        permissions=(),
        run=lambda i, c: Result(summary="changed", data={}, coverage="observed"),
    )
    demo.app.registry.add(change)
    refusal = demo.app.registry.refusal(change, demo.user, demo.env, demo.app.auth)
    assert refusal is not None
    assert "authorization path" in refusal.reason
    authorized = demo.env.model_copy(
        update={"settings": demo.env.settings.model_copy(update={"changes_authorized_by": "operator_role"})}
    )
    assert (
        demo.app.registry.refusal(change, demo.user, authorized, demo.app.auth) is None
    )  # the demo user is an operator
    investigator = demo.app.auth.create_user(
        "inv2", "a strong one", grants=[Grant(environment_id=demo.env.id, role="investigator")]
    )
    role_refusal = demo.app.registry.refusal(change, investigator, authorized, demo.app.auth)
    assert role_refusal is not None
    assert role_refusal.kind == "role"
