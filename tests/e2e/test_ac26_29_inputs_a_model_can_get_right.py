"""Guarantees 26 to 29 (change 0002, piece A): errors that teach, a stuck model that stops, known values,
and the names models expect."""

from k8sholmes.records import AuditEntry, Call, Message
from k8sholmes.testing.model import calls, structured, text
from tests.e2e.conftest import Demo

EMPTY = {"answer": "a", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}
FRAME = {
    "question": "q",
    "namespaces": ["shop"],
    "resources": [],
    "period_hours": 24,
    "hypotheses": [],
    "intended_capabilities": ["k8s.list", "list_pvc"],
}


def scripted(demo: Demo, rounds: list[list[tuple[str, dict]]]):
    """A model that frames, then asks for each round of tool calls in turn, then stops and concludes."""
    from k8sholmes.testing import ScriptedModel

    pending = list(rounds)

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            return structured(EMPTY)
        return calls(*pending.pop(0)) if pending else text("done")

    model = ScriptedModel(script=script)
    demo.app.engine.model_for = lambda endpoint_id: model
    return model


def calls_of(demo: Demo, inv_id: str) -> list[Call]:
    """The calls the model asked for (discovery's inventory is the engine's own)."""
    found = demo.app.store.find(Call, {"investigation_id": inv_id}, order_by="at")
    return [c for c in found if c.capability_id != "k8s.inventory"]


def test_ac26_an_invalid_call_is_told_how_to_fix_it_in_plain_text(demo: Demo) -> None:
    model = scripted(demo, [[("k8s.list", {"kind": "pods", "namespace": "shp", "label_sel": "app=api"})]])
    inv = demo.ask("what runs in shop?")
    [call] = calls_of(demo, inv.id)
    assert call.status == "invalid"
    assert "label_sel: Extra inputs are not permitted" in call.reason  # the field is named (guarantee 2)
    assert 'did you mean "selector"' in call.reason.replace("'", '"')
    # every problem in one message: the field and the namespace (listed from the cluster this run)
    assert "'shp' is not a namespace in this environment; did you mean 'shop'?" in call.reason
    assert "accepted fields: kind (required), namespace, selector, field_selector" in call.reason
    tool = [m for m in demo.app.store.find(Message, {"investigation_id": inv.id}) if m.role == "tool"]
    assert tool[0].content.startswith("Not run (invalid). k8s.list: invalid inputs")  # text, not JSON
    seen = [m for r in model.requests for m in r["messages"] if m.get("role") == "tool"]
    assert "accepted fields" in seen[0]["content"]  # the model saw the teaching message


def test_ac27_three_invalid_calls_in_a_row_stop_gathering_with_a_notice(demo: Demo) -> None:
    bad = ("k8s.list", {"kind": "pods", "class": "none"})
    events: list[tuple[str, dict]] = []
    demo.app.engine.on_event = lambda kind, payload: events.append((kind, payload))
    model = scripted(demo, [[bad], [bad], [bad], [("k8s.list", {"kind": "pods"})]])
    inv = demo.ask("what runs?")
    assert inv.status == "partial"  # it concluded with what it had; not failed
    assert [c.status for c in calls_of(demo, inv.id)] == ["invalid"] * 3  # the fourth round never ran
    [notice] = inv.notices
    assert notice.kind == "stopped_invalid"
    assert "invalid inputs (k8s.list) 3 times in a row" in notice.text
    assert notice.call_ids == [c.id for c in calls_of(demo, inv.id)]
    assert "the model could not form valid inputs for k8s.list" in inv.limitations
    assert any(kind == "stopped_invalid" for kind, _ in events)
    assert any(r["schema"] and r["schema"].get("title") == "conclusion" for r in model.requests)
    # a resume starts a fresh count: one more invalid call, then a valid one, and gathering goes on
    resumed = demo.app.engine.resume(inv.id, demo.user)
    statuses = [c.status for c in calls_of(demo, inv.id)]
    assert statuses == ["invalid"] * 3 + ["completed"]
    assert [n.superseded for n in resumed.notices] == [True]  # kept as history, no longer shown as current


def test_ac27_three_invalid_calls_in_one_reply_stop_and_name_each_capability(demo: Demo) -> None:
    scripted(demo, [[("k8s.list", {"kind": "pods", "x": 1}), ("k8s.get", {"y": 1}), ("k8s.list", {"z": 1})]])
    inv = demo.ask("what runs?")
    [notice] = inv.notices
    assert "invalid inputs (k8s.get, k8s.list) 3 times" in notice.text
    assert "the model could not form valid inputs for k8s.get, k8s.list" in inv.limitations


def test_ac27_a_valid_call_resets_the_count(demo: Demo) -> None:
    bad = ("k8s.list", {"kind": "pods", "class": "none"})
    scripted(demo, [[bad, bad], [("k8s.list", {"kind": "pods"})], [bad, bad]])
    inv = demo.ask("what runs?")
    assert not inv.notices
    assert [c.status for c in calls_of(demo, inv.id)].count("invalid") == 4


def test_ac28_validation_comes_before_the_allow_list_and_the_gate_still_refuses(demo: Demo) -> None:
    from k8sholmes.records import CapabilitySettings

    settings = demo.env.settings.model_copy(deep=True)
    settings.capabilities["k8s.events"] = CapabilitySettings(namespaces=["elsewhere"])
    demo.app.store.put(demo.env.model_copy(update={"settings": settings}))
    scripted(demo, [[("k8s.events", {"namespace": "shop"}), ("k8s.events", {"namespace": "nowhere"})]])
    inv = demo.ask("what happened?")
    known_outside, unknown = calls_of(demo, inv.id)
    assert known_outside.status == "refused"  # exists, outside the allow-list: the gate
    assert "limited to elsewhere" in known_outside.reason
    assert unknown.status == "invalid"  # does not exist: validation, before the gate
    assert "'nowhere' is not a namespace in this environment" in unknown.reason


def test_ac28_the_model_sees_known_values_and_validation_checks_them(demo: Demo) -> None:
    settings = demo.env.settings.model_copy(deep=True)
    from k8sholmes.records import CapabilitySettings

    settings.capabilities["k8s.events"] = CapabilitySettings(namespaces=["shop"])
    demo.app.store.put(demo.env.model_copy(update={"settings": settings}))
    model = scripted(demo, [[("k8s.list", {"kind": "Gadget"}), ("k8s.events", {"since_hours": 1})]])
    inv = demo.ask("what runs?")
    tools = {t["function"]["name"]: t["function"]["parameters"] for t in model.requests[1]["tools"]}
    kinds = tools["k8s.list"]["properties"]["kind"]["enum"]
    assert kinds[:2] == ["Pod", "Deployment"] and "Secret" in kinds
    namespaces = tools["k8s.list"]["properties"]["namespace"]["anyOf"][0]["enum"]
    assert "shop" in namespaces
    events_ns = tools["k8s.events"]["properties"]["namespace"]["anyOf"][0]["enum"]
    assert events_ns == ["shop"]  # the allow-list narrows what the model is shown
    framing = model.requests[0]["schema"]["properties"]["intended_capabilities"]["items"]["enum"]
    assert "k8s.list" in framing and "list_pvc" not in framing
    assert inv.framing is not None and inv.framing.intended_capabilities == ["k8s.list"]
    by_cap = {c.capability_id: c for c in calls_of(demo, inv.id)}
    assert by_cap["k8s.list"].status == "invalid"
    assert "'Gadget' is not a kind this cluster serves" in by_cap["k8s.list"].reason
    assert by_cap["k8s.events"].status == "refused"  # every namespace, outside the allow-list: the gate


def test_ac28_an_unknown_namespace_is_refused_with_a_suggestion(demo: Demo) -> None:
    scripted(demo, [[("k8s.list", {"kind": "pods", "namespace": "shpo"})]])
    inv = demo.ask("what runs?")
    [call] = calls_of(demo, inv.id)
    assert call.status == "invalid"
    assert "'shpo' is not a namespace in this environment; did you mean 'shop'?" in call.reason
    assert "(namespaces listed at the start of this run)" in call.reason


def test_ac29_aliases_and_kind_names_are_accepted_and_what_was_sent_is_kept(demo: Demo) -> None:
    scripted(
        demo,
        [
            [("k8s.list", {"kind": "po", "ns": "shop", "label_selector": "app=api"})],
            [("k8s.list", {"kind": "pods", "selector": "app=api", "label_selector": "app=web"})],
        ],
    )
    inv = demo.ask("what runs?")
    first, second = calls_of(demo, inv.id)
    assert first.status == "completed"
    assert first.inputs == {"kind": "Pod", "namespace": "shop", "selector": "app=api"}
    assert first.received == {"kind": "po", "ns": "shop", "label_selector": "app=api"}
    assert second.status == "invalid"  # both names sent: the alias is not guessed; guarantee 2 holds
    assert "label_selector: Extra inputs are not permitted" in second.reason


def test_resume_with_another_model_is_recorded_in_the_thread_and_the_audit_log(demo: Demo) -> None:
    scripted(demo, [])
    inv = demo.ask("what runs?")
    before = inv.model_endpoint_id
    changed = demo.app.engine.change_model(inv.id, demo.user, "mdl_other")
    assert changed.model_endpoint_id == "mdl_other"
    notes = [m for m in demo.app.store.find(Message, {"investigation_id": inv.id}) if m.role == "user"]
    assert "changed the model endpoint" in notes[-1].content
    [entry] = [a for a in demo.app.store.find(AuditEntry) if a.action == "investigation_model_changed"]
    assert entry.details == {"from": before, "to": "mdl_other"}
    assert demo.app.engine.change_model(inv.id, demo.user, "mdl_other") == changed  # no change: nothing
