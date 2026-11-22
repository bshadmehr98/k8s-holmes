"""Guarantee 30 (change 0002, piece C): a playbook's calls run through the gate, are recorded with the
playbook's id, and the conclusion still says what the cluster cannot answer."""

from k8sholmes.records import Call, CapabilitySettings, Message, Step
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.model import structured, text
from tests.e2e.conftest import Demo

EMPTY = {"answer": "a", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}


def frame(playbook: str) -> dict:
    return {
        "question": "is anything unused?",
        "namespaces": ["shop"],
        "resources": [],
        "period_hours": 24,
        "hypotheses": [],
        "intended_capabilities": [],
        "playbook": playbook,
    }


def scripted(demo: Demo, playbook: str = "unused", **kwargs) -> ScriptedModel:
    def script(messages, tools, schema):
        title = (schema or {}).get("title")
        if title == "framing":
            return structured(frame(playbook))
        if title == "choose":
            return text('{"action": "done", "capability": "done", "why": "the playbook was enough"}')
        if schema:
            return structured(EMPTY)
        return text("done")  # native: the model asks for nothing more

    model = ScriptedModel(script=script, **kwargs)
    demo.app.engine.model_for = lambda endpoint_id: model
    return model


def by_playbook(demo: Demo, inv_id: str) -> list[Call]:
    return [c for c in demo.app.store.find(Call, {"investigation_id": inv_id}, order_by="at") if c.playbook]


def test_ac30_the_unused_playbook_runs_through_the_gate_and_says_what_it_cannot_know(demo: Demo) -> None:
    settings = demo.env.settings.model_copy(deep=True)
    settings.capabilities["k8s.list"] = CapabilitySettings(enabled=False)
    demo.app.store.put(demo.env.model_copy(update={"settings": settings}))
    model = scripted(demo)
    inv = demo.ask("Is anything unused?")
    framing_schema = model.requests[0]["schema"]
    assert framing_schema["properties"]["playbook"]["enum"] == [
        "unused",
        "restarts",
        "exposure",
        "what_changed",
        "none",
    ]
    assert inv.framing is not None and inv.framing.playbook == "unused" and inv.playbook == "unused"
    ran = by_playbook(demo, inv.id)
    assert {c.playbook for c in ran} == {"unused"}
    caps = [c.capability_id for c in ran]
    assert caps[0] == "k8s.inventory" and caps.count("k8s.service_backends") >= 2  # one per Service in shop
    listed = [c for c in ran if c.capability_id == "k8s.list"]
    assert listed and all(
        c.status == "refused" for c in listed
    )  # disabled: the gate refuses the playbook too
    assert any(lim.startswith("refused: k8s.list is disabled") for lim in inv.limitations)
    assert any(lim.startswith("traffic: no connected source records requests") for lim in inv.limitations)
    assert inv.status == "partial"
    note = [
        m for m in demo.app.store.find(Message, {"investigation_id": inv.id}) if m.author == "playbook:unused"
    ]
    assert note and "run by K8sHolmes, not the model" in note[0].content
    steps = demo.app.store.find(Step, {"investigation_id": inv.id, "phase": "gathering"})
    assert min(steps, key=lambda s: s.seq).summary.startswith("playbook unused:")


def test_a_playbook_runs_once_and_never_on_a_follow_up(demo: Demo) -> None:
    scripted(demo, "exposure")
    inv = demo.ask("What is exposed?")
    first = len(by_playbook(demo, inv.id))
    assert first == 2 and any(lim.startswith("beyond the cluster") for lim in inv.limitations)
    demo.app.store.put(inv.model_copy(update={"status": "cancelled"}))
    demo.app.engine.resume(inv.id, demo.user)
    demo.app.engine.follow_up(inv.id, demo.user, "and the ingresses?")
    assert len(by_playbook(demo, inv.id)) == first


def test_none_runs_no_playbook_and_structured_mode_sees_results_as_data(demo: Demo) -> None:
    scripted(demo, "none")
    inv = demo.ask("q")
    assert inv.playbook is None and not by_playbook(demo, inv.id)
    scripted(demo, "what_changed", tool_mode="structured")
    inv = demo.ask("What changed?")
    assert {c.capability_id for c in by_playbook(demo, inv.id)} == {"k8s.changes", "k8s.events"}
    results = [
        m
        for m in demo.app.store.find(Message, {"investigation_id": inv.id})
        if m.author.startswith("capability:")
    ]
    assert results and all(m.role == "user" and m.content.startswith("Result of") for m in results)


def test_an_interrupted_playbook_is_not_repeated_on_resume(demo: Demo) -> None:
    import pytest

    scripted(demo, "unused")
    engine = demo.app.engine
    real = engine.registry.execute
    count = {"n": 0}

    def dies(*args, **kwargs):
        count["n"] += 1
        if count["n"] == 4:  # discovery's inventory, then two playbook calls, then the process dies
            raise RuntimeError("process died")
        return real(*args, **kwargs)

    engine.registry.execute = dies
    inv = engine.start(environment=demo.env, question="Is anything unused?", user=demo.user)
    with pytest.raises(RuntimeError):
        engine.run(inv.id, demo.user)
    engine.registry.execute = real
    before = len(by_playbook(demo, inv.id))
    resumed = engine.resume(inv.id, demo.user)
    assert len(by_playbook(demo, inv.id)) == before == 2  # nothing repeated
    assert resumed.status in ("completed", "partial")


def test_cancel_and_budgets_stop_a_playbook_between_calls(demo: Demo) -> None:
    from k8sholmes.records import Budgets

    scripted(demo, "unused")
    engine = demo.app.engine
    real = engine.registry.execute
    state: dict = {}

    def cancels(*args, **kwargs):
        result = real(*args, **kwargs)
        if args[0] == "k8s.list" and "inv" in state:
            engine.cancel(state["inv"], demo.user)
        return result

    engine.registry.execute = cancels
    inv = engine.start(environment=demo.env, question="Is anything unused?", user=demo.user)
    state["inv"] = inv.id
    inv = engine.run(inv.id, demo.user)
    engine.registry.execute = real
    assert inv.status == "cancelled"
    assert [c.capability_id for c in by_playbook(demo, inv.id)] == ["k8s.inventory", "k8s.list"]
    scripted(demo, "unused")
    tight = engine.start(environment=demo.env, question="q", user=demo.user, budgets=Budgets(steps=2))
    tight = engine.run(tight.id, demo.user)
    assert not by_playbook(demo, tight.id)  # the step budget was spent before its first call
    assert any("step budget (2)" in lim for lim in tight.limitations)


def test_the_model_sees_the_playbook_results_and_its_limitation_follows_the_sources(demo: Demo) -> None:
    from k8sholmes.capabilities import Capability
    from k8sholmes.capabilities.kubernetes import NoInputs
    from k8sholmes.environments import latest_inventory
    from k8sholmes.evidence import Result

    model = scripted(demo, "unused")
    inv = demo.ask("Is anything unused?")
    latest = latest_inventory(demo.app.store, demo.env.id)
    assert latest is not None
    shop_services = sorted(
        i.ref.name for i in latest.items if i.ref.kind == "Service" and i.ref.namespace == "shop"
    )
    ran = by_playbook(demo, inv.id)
    assert sorted(c.inputs["name"] for c in ran if c.capability_id == "k8s.service_backends") == shop_services
    first = next(r for r in model.requests if r["tools"])["messages"]
    assert any(m["role"] == "assistant" and "Playbook unused" in str(m["content"]) for m in first)
    assert sum(m["role"] == "tool" for m in first) == len(ran)
    traffic = Capability(
        id="mesh.requests",
        title="Requests",
        purpose="Requests between services.",
        class_="inspect",
        systems=("traffic",),
        inputs=NoInputs,
        permissions=(),
        run=lambda i, c: Result(summary="none", data={}, coverage="observed"),
    )
    demo.app.engine.registry.add(traffic)
    scripted(demo, "unused")
    with_source = demo.ask("Is anything unused?")
    assert not any(lim.startswith("traffic: no connected source") for lim in with_source.limitations)
