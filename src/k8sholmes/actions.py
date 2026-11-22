"""The structured tool mode (change 0002, parts 1 and 4): each gathering step as two small JSON decisions.

**choose**: the next capability, from an enum of the available ids with the framing's intended ones
first, or `done`. **fill**: that one capability's inputs, under its own schema. Both go out as structured
replies (`response_format`), so a server that constrains decoding cannot return broken JSON; a reply that
is still not the object asked for gets one repair request. Code turns the pair into a `ToolCall` that the
engine runs like any other: validation, the gate, the recorded call and evidence (design §5.2).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from k8sholmes.capabilities import Capability
from k8sholmes.capabilities.inputs import Known
from k8sholmes.records import ToolCall, new_id
from k8sholmes.thread import json_object

DONE = "done"
# ask(messages, schema, step) -> the reply's text; the engine records the call and its usage
Ask = Callable[[list[dict[str, Any]], dict[str, Any], str], str]
Problem = Callable[[dict[str, Any]], str | None]  # what is wrong with a reply, or None


def _not_empty(payload: dict[str, Any]) -> str | None:
    return None if payload else "the reply was not a JSON object"


def ordered(available: list[Capability], intended: list[str]) -> list[Capability]:
    """The framing's intended capabilities first, in its order; the rest as the registry lists them."""
    rank = {cid: i for i, cid in enumerate(intended)}
    return sorted(available, key=lambda c: rank.get(c.id, len(rank)))


def choose_schema(available: list[Capability]) -> dict[str, Any]:
    return {
        "title": "choose",
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "capability", "why"],
        "properties": {
            "action": {"type": "string", "enum": ["call", DONE]},
            "capability": {"type": "string", "enum": [*(c.id for c in available), DONE]},
            "why": {"type": "string", "description": "One sentence: what this call should show."},
        },
    }


def choose_message(available: list[Capability]) -> dict[str, Any]:
    lines = [f"- {c.id}: {c.title}. {c.purpose.splitlines()[0]}" for c in available]
    return {
        "role": "user",
        "content": "Choose the next capability to run, or answer done when you have what you need. "
        "Answer with the JSON object only.\nCapabilities:\n" + "\n".join(lines),
    }


def fill_schema(cap: Capability, known: Known | None, allow: list[str]) -> dict[str, Any]:
    return {"title": "inputs", **cap.schema(known, allow)}


def fill_message(cap: Capability) -> dict[str, Any]:
    return {
        "role": "user",
        "content": f"Give the inputs for {cap.id} ({cap.title}) as the JSON object only. {cap.purpose}",
    }


def _choice_problem(payload: dict[str, Any], ids: list[str]) -> str | None:
    if not payload:
        return "the reply was not a JSON object"
    if payload.get("action") == DONE or payload.get("capability") == DONE:
        return None
    if payload.get("capability") not in ids:
        return f"capability must be one of {', '.join(ids)} or done"
    return None


def _ask_json(
    ask: Ask, messages: list[dict[str, Any]], schema: dict[str, Any], step: str, problem: Problem
) -> dict[str, Any]:
    """The reply as a dict, with one repair request when it is not the object asked for."""
    content = ask(messages, schema, step)
    payload = json_object(content)
    issue = problem(payload)
    if issue:
        repair = [
            *messages,
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": f"That was not right: {issue}. Answer again with the JSON object only.",
            },
        ]
        content = ask(repair, schema, step)
        payload = json_object(content)
    return payload


def structured_turn(
    ask: Ask,
    messages: list[dict[str, Any]],
    available: list[Capability],
    known: Known | None,
    allow_for: Callable[[Capability], list[str]],
) -> tuple[ToolCall | None, str]:
    """One gathering step: choose, then fill. Returns the call to run (None: the model is done) and the
    text kept in the thread as the model's turn."""
    ids = [c.id for c in available]
    choice = _ask_json(
        ask,
        [*messages, choose_message(available)],
        choose_schema(available),
        "choose",
        lambda payload: _choice_problem(payload, ids),
    )
    why = str(choice.get("why") or "")
    if choice.get("action") == DONE or choice.get("capability") == DONE:
        return None, json.dumps({"action": DONE, "why": why})
    chosen = next((c for c in available if c.id == choice.get("capability")), None)
    if chosen is None:  # still not a capability after the repair: an invalid call, counted like any other
        name = str(choice.get("capability") or "(no capability chosen)")
        return ToolCall(id=new_id("act"), name=name, arguments={}), json.dumps(
            {"action": "call", "capability": name, "why": why}
        )
    turn = [
        *messages,
        choose_message(available),
        {"role": "assistant", "content": json.dumps(choice)},
        fill_message(chosen),
    ]
    inputs = _ask_json(
        ask,
        turn,
        fill_schema(chosen, known, allow_for(chosen)),
        "fill",
        _not_empty,
    )
    action = {"action": "call", "capability": chosen.id, "inputs": inputs, "why": why}
    return ToolCall(id=new_id("act"), name=chosen.id, arguments=inputs), json.dumps(action)
