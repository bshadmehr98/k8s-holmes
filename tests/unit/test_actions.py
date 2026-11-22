"""The structured tool mode's two small decisions (change 0002, parts 1 and 4)."""

import json
from typing import Any

import pytest

from k8sholmes.actions import choose_schema, ordered, structured_turn
from k8sholmes.capabilities.builtin import builtin_registry
from k8sholmes.errors import InvalidInput

REGISTRY = builtin_registry(extensions=False)
AVAILABLE = [REGISTRY.get(c) for c in ("k8s.get", "k8s.list", "k8s.events")]


def asker(replies: list[str]):
    """An `ask` that answers in turn and keeps what it was asked."""
    asked: list[tuple[list[dict[str, Any]], dict[str, Any], str]] = []

    def ask(messages: list[dict[str, Any]], schema: dict[str, Any], step: str) -> str:
        asked.append((messages, schema, step))
        return replies.pop(0)

    return ask, asked


def turn(replies: list[str]):
    ask, asked = asker(replies)
    call, text = structured_turn(ask, [{"role": "user", "content": "q"}], AVAILABLE, None, lambda c: [])
    return call, text, asked


def test_intended_capabilities_come_first_and_the_choice_is_an_enum() -> None:
    assert [c.id for c in ordered(AVAILABLE, ["k8s.events", "nope"])] == ["k8s.events", "k8s.get", "k8s.list"]
    schema = choose_schema(AVAILABLE)
    assert schema["properties"]["capability"]["enum"] == ["k8s.get", "k8s.list", "k8s.events", "done"]


def test_choose_then_fill_gives_one_call_and_the_fill_sees_one_schema() -> None:
    choice = json.dumps({"action": "call", "capability": "k8s.list", "why": "see pods"})
    call, text, asked = turn([choice, json.dumps({"kind": "pods"})])
    assert call is not None and call.name == "k8s.list" and call.arguments == {"kind": "pods"}
    assert [step for _, _, step in asked] == ["choose", "fill"]
    fill_schema = asked[1][1]
    assert fill_schema["title"] == "inputs" and set(fill_schema["properties"]) == {
        "kind",
        "namespace",
        "selector",
        "field_selector",
    }
    assert json.loads(text) == {
        "action": "call",
        "capability": "k8s.list",
        "inputs": {"kind": "pods"},
        "why": "see pods",
    }


def test_done_ends_gathering() -> None:
    call, text, asked = turn([json.dumps({"action": "done", "capability": "done", "why": "enough"})])
    assert call is None and json.loads(text)["action"] == "done" and len(asked) == 1


def test_a_bad_choice_is_repaired_once_then_becomes_an_invalid_call() -> None:
    good = json.dumps({"action": "call", "capability": "k8s.get", "why": ""})
    call, _, asked = turn(["not json", good, json.dumps({"kind": "pods", "name": "a"})])
    assert call is not None and call.name == "k8s.get"
    assert "That was not right: the reply was not a JSON object" in asked[1][0][-1]["content"]
    wrong = json.dumps({"action": "call", "capability": "k8s.exec", "why": ""})
    call, _, asked = turn([wrong, wrong])
    assert call is not None and call.name == "k8s.exec" and call.arguments == {}  # the registry refuses it
    assert "capability must be one of" in asked[1][0][-1]["content"]


def test_unparsable_inputs_after_the_repair_are_sent_for_validation_to_refuse() -> None:
    choice = json.dumps({"action": "call", "capability": "k8s.list", "why": ""})
    call, _, asked = turn([choice, "oops", "still not"])
    assert call is not None and call.arguments == {"(not a JSON object)": "still not"}
    assert [step for _, _, step in asked] == ["choose", "fill", "fill"]
    with pytest.raises(InvalidInput, match="- the inputs were not a JSON object"):
        REGISTRY.validate(REGISTRY.get("k8s.list"), call.arguments)


def test_an_empty_object_is_an_answer_for_a_capability_without_inputs() -> None:
    ask, asked = asker([json.dumps({"action": "call", "capability": "k8s.permissions", "why": ""}), "{}"])
    only = [REGISTRY.get("k8s.permissions")]
    call, _ = structured_turn(ask, [], only, None, lambda c: [])
    assert call is not None and call.arguments == {}
    assert [step for _, _, step in asked] == ["choose", "fill"]  # no repair
