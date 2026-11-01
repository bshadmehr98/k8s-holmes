"""`ScriptedModel`: a `Model` whose replies come from a script, for tests and the demo."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from k8sholmes.models import Reply
from k8sholmes.records import ToolCall

Script = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None, dict[str, Any] | None], Reply]


def text(content: str) -> Reply:
    return Reply(content=content, finish_reason="stop", prompt_tokens=100, completion_tokens=20)


def calls(*tool_calls: tuple[str, dict[str, Any]]) -> Reply:
    return Reply(
        tool_calls=[
            ToolCall(id=f"call_{n}", name=name, arguments=args) for n, (name, args) in enumerate(tool_calls)
        ],
        finish_reason="tool_calls",
        prompt_tokens=100,
        completion_tokens=30,
    )


def structured(payload: dict[str, Any]) -> Reply:
    return Reply(
        content=json.dumps(payload),
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=50,
        structured_supported=True,
    )


class ScriptedModel:
    """Replies in order from `replies`, or from `script(messages, tools, schema)` when given.

    Every request is kept in `requests` so a test can check what the model saw (and did not see).
    """

    def __init__(
        self, replies: list[Reply] | None = None, *, script: Script | None = None, name: str = "scripted"
    ) -> None:
        self.name = name
        self._replies = list(replies or [])
        self._script = script
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> Reply:
        self.requests.append(
            {
                "messages": messages,
                "tools": tools,
                "schema": schema,
                "max_tokens": max_tokens,
                "stream": stream,
            }
        )
        if self._script is not None:
            reply = self._script(messages, tools, schema)
        elif self._replies:
            reply = self._replies.pop(0)
        else:
            reply = text("I have nothing more to say.")
        if stream and on_delta and reply.content:
            on_delta(reply.content)
        return reply

    def saw(self, needle: str) -> bool:
        """Whether any message of any request contained `needle`."""
        return any(needle in json.dumps(r["messages"]) for r in self.requests)


def check_model(model: Any) -> None:
    """The contract every `Model` keeps (design §13.1): raises AssertionError. Offline-safe for fakes."""
    reply = model.complete([{"role": "user", "content": "Say ok."}], max_tokens=8)
    assert isinstance(reply, Reply), "complete returns a Reply"
    assert isinstance(reply.content, str), "content is text"
    assert all(isinstance(tc, ToolCall) and isinstance(tc.arguments, dict) for tc in reply.tool_calls), (
        "tool calls are typed"
    )
    assert min(reply.prompt_tokens, reply.completion_tokens, reply.duration_ms) >= 0, "usage is counted"
    assert isinstance(model.name, str), "a model has a name"
    assert model.name, "a model has a name"
