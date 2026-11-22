"""The structured tool mode (change 0002, parts 1 and 4): each gathering step as two small JSON decisions.

**choose**: the next capability, from an enum of the available ids with the framing's intended ones
first, or `done`. **fill**: that one capability's inputs, under its own schema. Both go out as structured
replies (`response_format`), so a server that constrains decoding cannot return broken JSON; a reply that
is still not the object asked for gets one repair request. Code turns the pair into a `ToolCall` that the
engine runs like any other: validation, the gate, the recorded call and evidence (design §5.2).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from k8sholmes.capabilities import Capability
from k8sholmes.capabilities.inputs import NOT_JSON, Known
from k8sholmes.records import ToolCall, new_id
from k8sholmes.thread import json_object

DONE = "done"
# ask(messages, schema, step) -> the reply's text; the engine records the call and its usage
Ask = Callable[[list[dict[str, Any]], dict[str, Any], str], str]
Problem = Callable[[dict[str, Any] | None], str | None]  # what is wrong with a reply, or None
NOT_AN_OBJECT = "the reply was not a JSON object"


def parsed(text: str) -> dict[str, Any] | None:
    """The reply's JSON object, or None when there is none: `{}` is an answer, prose is not."""
    payload = json_object(text)
    return payload if payload or re.search(r"\{\s*\}", text) else None


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


def fill_message(cap: Capability) -> dict[str, Any]:
    return {
        "role": "user",
        "content": f"Give the inputs for {cap.id} ({cap.title}) as the JSON object only. {cap.purpose}",
    }


def _is_done(choice: dict[str, Any]) -> bool:
    return choice.get("action") == DONE or choice.get("capability") == DONE


def _choice_problem(payload: dict[str, Any] | None, ids: list[str]) -> str | None:
    if not payload:
        return NOT_AN_OBJECT
    if not _is_done(payload) and payload.get("capability") not in ids:
        return f"capability must be one of {', '.join(ids)} or done"
    return None


def _fill_problem(payload: dict[str, Any] | None) -> str | None:
    return NOT_AN_OBJECT if payload is None else None


def _ask_json(
    ask: Ask, messages: list[dict[str, Any]], schema: dict[str, Any], step: str, problem: Problem
) -> tuple[dict[str, Any] | None, str]:
    """The reply as a dict (None: no JSON object) and its text, with one repair request when it is not
    the object asked for."""
    content = ask(messages, schema, step)
    payload = parsed(content)
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
        payload = parsed(content)
    return payload, content


def structured_turn(
    ask: Ask,
    messages: list[dict[str, Any]],
    available: list[Capability],
    known: Known | None,
    allow_for: Callable[[Capability], list[str]],
) -> tuple[ToolCall | None, str]:
    """One gathering step: choose, then fill. Returns the call to run (None: the model is done) and the
    text kept in the thread as the model's turn. A choice or inputs that are still wrong after the repair
    become a call the registry records as invalid, counted like any other."""
    ids = [c.id for c in available]
    asking = [*messages, choose_message(available)]
    choice, _ = _ask_json(ask, asking, choose_schema(available), "choose", lambda p: _choice_problem(p, ids))
    choice = choice or {}
    why = str(choice.get("why") or "")
    if _is_done(choice):
        return None, json.dumps({"action": DONE, "why": why})
    chosen = next((c for c in available if c.id == choice.get("capability")), None)
    if chosen is None:
        name = str(choice.get("capability") or "(no capability chosen)")
        action = {"action": "call", "capability": name, "why": why}
        return ToolCall(id=new_id("act"), name=name, arguments={}), json.dumps(action)
    turn = [*asking, {"role": "assistant", "content": json.dumps(choice)}, fill_message(chosen)]
    schema = {"title": "inputs", **chosen.schema(known, allow_for(chosen))}
    inputs, content = _ask_json(ask, turn, schema, "fill", _fill_problem)
    arguments = inputs if inputs is not None else {NOT_JSON: content[:200]}
    action = {"action": "call", "capability": chosen.id, "inputs": arguments, "why": why}
    return ToolCall(id=new_id("act"), name=chosen.id, arguments=arguments), json.dumps(action)
