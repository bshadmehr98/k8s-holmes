"""The `Model` port, the OpenAI-compatible client and the capability tests (design §13).

The client speaks the chat completions API over `urllib`: tool calls, streaming (server-sent events) and
structured replies (`response_format` with a JSON schema). Credentials come from the environment or the
secrets file and travel only in the Authorization header. Failures are typed: retryable (timeouts, 429,
5xx, a dropped connection) or permanent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from k8sholmes.errors import InvalidInput, ModelFailure
from k8sholmes.records import CapabilityTestResult, ModelEndpoint, ToolCall


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list[ToolCall])
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    streamed: bool = False
    structured_supported: bool | None = None  # None: not asked


class Model(Protocol):
    """One structured call: messages in, a reply out, with usage. The engine knows nothing else."""

    name: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> Reply: ...


# secrets


def secrets_path(home: Path) -> Path:
    return home / "secrets.json"


def read_secrets(home: Path) -> dict[str, str]:
    path = secrets_path(home)
    if not path.exists():
        return {}
    return cast(dict[str, str], json.loads(path.read_text()))


def write_secret(home: Path, endpoint_id: str, key: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = secrets_path(home)
    secrets = read_secrets(home)
    secrets[endpoint_id] = key
    path.write_text(json.dumps(secrets, indent=1))
    path.chmod(0o600)


def api_key_for(endpoint: ModelEndpoint, home: Path | None) -> str | None:
    if endpoint.api_key_env:
        return os.environ.get(endpoint.api_key_env) or None
    if home is not None:
        return read_secrets(home).get(endpoint.id)
    return None


# the client


def tool_definition(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


class OpenAICompatible:
    """The chat completions API of any OpenAI-compatible endpoint (design §13.1, D-009)."""

    def __init__(self, endpoint: ModelEndpoint, api_key: str | None) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.name = endpoint.model

    def _request(self, path: str, body: dict[str, Any] | None, *, stream: bool = False) -> Any:
        url = self.endpoint.base_url.rstrip("/") + path
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.endpoint.headers)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310
            url, data=data, headers=headers, method="POST" if body is not None else "GET"
        )
        timeout = self.endpoint.limits.timeout_ms / 1000
        try:
            return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")[:500]
            retryable = exc.code == 429 or exc.code >= 500
            raise ModelFailure(
                f"{self.endpoint.name} answered HTTP {exc.code}: {text}", retryable=retryable, status=exc.code
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ModelFailure(
                f"{self.endpoint.name} could not be reached: {reason}", retryable=True
            ) from exc

    def list_models(self) -> list[str]:
        with self._request("/models", None) as response:
            payload = json.loads(response.read())
        return [str(m.get("id")) for m in cast(list[dict[str, Any]], payload.get("data", []))]

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
        body: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "max_tokens": max_tokens or self.endpoint.limits.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": str(schema.get("title", "reply")), "schema": schema},
            }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        started = time.monotonic()
        try:
            with self._request("/chat/completions", body, stream=stream) as response:
                reply = (
                    self._read_stream(response, on_delta)
                    if stream
                    else self._read_json(json.loads(response.read()))
                )
        except ModelFailure as exc:
            if schema is not None and exc.status == 400 and "response_format" in str(exc):
                body.pop("response_format")
                body["messages"] = [
                    *messages,
                    {
                        "role": "user",
                        "content": "Answer with JSON only, matching this schema:\n" + json.dumps(schema),
                    },
                ]
                with self._request("/chat/completions", body) as response:
                    reply = self._read_json(json.loads(response.read()))
                reply.structured_supported = False
            else:
                raise
        else:
            if schema is not None:
                reply.structured_supported = True
        reply.duration_ms = int((time.monotonic() - started) * 1000)
        reply.streamed = stream
        return reply

    @staticmethod
    def _read_json(payload: dict[str, Any]) -> Reply:
        choices = cast(list[dict[str, Any]], payload.get("choices") or [])
        if not choices:
            raise ModelFailure("the model returned no choices", retryable=False)
        message = cast(dict[str, Any], choices[0].get("message") or {})
        usage = cast(dict[str, Any], payload.get("usage") or {})
        return Reply(
            content=str(message.get("content") or ""),
            tool_calls=_tool_calls(cast(list[dict[str, Any]], message.get("tool_calls") or [])),
            finish_reason=str(choices[0].get("finish_reason") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    @staticmethod
    def _read_stream(response: Any, on_delta: Callable[[str], None] | None) -> Reply:
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish = ""
        usage: dict[str, Any] = {}
        for raw in response:
            line = raw.decode(errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = cast(dict[str, Any], json.loads(data))
            if chunk.get("usage"):
                usage = cast(dict[str, Any], chunk["usage"])
            for choice in cast(list[dict[str, Any]], chunk.get("choices") or []):
                delta = cast(dict[str, Any], choice.get("delta") or {})
                if delta.get("content"):
                    content.append(str(delta["content"]))
                    if on_delta:
                        on_delta(str(delta["content"]))
                for tc in cast(list[dict[str, Any]], delta.get("tool_calls") or []):
                    index = int(tc.get("index", 0))
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    function = cast(dict[str, Any], tc.get("function") or {})
                    slot["name"] = function.get("name") or slot["name"]
                    slot["arguments"] += str(function.get("arguments") or "")
                if choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
        tool_calls = _tool_calls(
            [
                {"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}}
                for _, c in sorted(calls.items())
            ]
        )
        return Reply(
            content="".join(content),
            tool_calls=tool_calls,
            finish_reason=finish,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


def _tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for n, tc in enumerate(raw):
        function = cast(dict[str, Any], tc.get("function") or {})
        arguments_text = function.get("arguments") or "{}"
        try:
            arguments = (
                json.loads(arguments_text)
                if isinstance(arguments_text, str)
                else dict(cast(dict[str, Any], arguments_text))
            )
        except json.JSONDecodeError:
            arguments = {"_unparsed": arguments_text}
        if not isinstance(arguments, dict):
            arguments = {"_value": arguments}
        calls.append(
            ToolCall(
                id=str(tc.get("id") or f"call_{n}"),
                name=str(function.get("name") or ""),
                arguments=cast(dict[str, Any], arguments),
            )
        )
    return calls


def price_of(endpoint: ModelEndpoint, prompt_tokens: int, completion_tokens: int) -> float | None:
    if endpoint.price is None:
        return None
    return round(
        prompt_tokens / 1e6 * endpoint.price.input_per_million
        + completion_tokens / 1e6 * endpoint.price.output_per_million,
        6,
    )


# capability tests (design §13.2)

_TOOL = tool_definition(
    "get_time",
    "The current time in a city.",
    {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)
_SCHEMA = {
    "title": "answer",
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _test_connectivity(model: OpenAICompatible) -> tuple[str, str]:
    try:
        models = model.list_models()
    except ModelFailure as exc:
        if exc.status == 404:
            reply = model.complete([{"role": "user", "content": "Say ok."}], max_tokens=5)
            return "passed", f"no /models endpoint; a completion answered in {reply.duration_ms} ms"
        raise
    listed = (
        f"; {model.endpoint.model} among them"
        if model.endpoint.model in models
        else f"; {model.endpoint.model} not in the list"
    )
    return "passed", f"{len(models)} models listed{listed}"


def _test_authentication(model: OpenAICompatible) -> tuple[str, str]:
    reply = model.complete([{"role": "user", "content": "Say ok."}], max_tokens=5)
    return "passed", f"authenticated; {reply.prompt_tokens + reply.completion_tokens} tokens"


def _test_model(model: OpenAICompatible) -> tuple[str, str]:
    reply = model.complete([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=8)
    return ("passed" if reply.content.strip() else "failed"), f"reply: {reply.content.strip()[:80]!r}"


def _test_tool_calling(model: OpenAICompatible) -> tuple[str, str]:
    reply = model.complete(
        [{"role": "user", "content": "What time is it in Paris? Use the tool."}], tools=[_TOOL], max_tokens=64
    )
    if not reply.tool_calls:
        return "failed", f"the model answered in text instead of calling the tool: {reply.content[:200]!r}"
    call = reply.tool_calls[0]
    if call.name != "get_time" or "paris" not in str(call.arguments.get("city", "")).lower():
        return "failed", f"the model called {call.name}({call.arguments}) instead of get_time(city=Paris)"
    return "passed", f"called {call.name}({call.arguments})"


def _test_streaming(model: OpenAICompatible) -> tuple[str, str]:
    chunks: list[str] = []
    reply = model.complete(
        [{"role": "user", "content": "Count from one to five in words."}],
        max_tokens=40,
        stream=True,
        on_delta=chunks.append,
    )
    if not chunks and not reply.content:
        return "unsupported", "no streamed content arrived"
    return "passed", f"{len(chunks)} chunks"


def _test_structured(model: OpenAICompatible) -> tuple[str, str]:
    reply = model.complete(
        [{"role": "user", "content": "What is 6 times 7? Answer as JSON with the integer field answer."}],
        schema=_SCHEMA,
        max_tokens=40,
    )
    try:
        payload = json.loads(reply.content)
    except json.JSONDecodeError:
        return "failed", f"not JSON: {reply.content[:120]!r}"
    if payload.get("answer") != 42:
        return "failed", f"the schema was honoured but the answer was {payload!r}"
    if reply.structured_supported:
        return "passed", "response_format honoured"
    return "unsupported", "response_format rejected; a JSON instruction worked instead"


def _test_limits(model: OpenAICompatible) -> tuple[str, str]:
    reply = model.complete([{"role": "user", "content": "Write a long story about the sea."}], max_tokens=16)
    ok = reply.completion_tokens <= 24 or reply.finish_reason == "length"
    return (
        "passed" if ok else "failed"
    ), f"{reply.completion_tokens} completion tokens, finish_reason={reply.finish_reason!r}"


TESTS: dict[str, Callable[[OpenAICompatible], tuple[str, str]]] = {
    "connectivity": _test_connectivity,
    "authentication": _test_authentication,
    "model": _test_model,
    "tool_calling": _test_tool_calling,
    "streaming": _test_streaming,
    "structured": _test_structured,
    "limits": _test_limits,
}


def run_capability_tests(model: OpenAICompatible) -> dict[str, CapabilityTestResult]:
    """Run every capability test; each is passed, failed (with the response) or unsupported."""
    results: dict[str, CapabilityTestResult] = {}
    for name, fn in TESTS.items():
        if name != "connectivity" and results["connectivity"].status == "failed":
            results[name] = CapabilityTestResult(status="failed", detail="not run: connectivity failed")
            continue
        try:
            status, detail = fn(model)
        except ModelFailure as exc:
            status, detail = "failed", (f"HTTP {exc.status}: {exc}" if exc.status else str(exc))
        results[name] = CapabilityTestResult(status=cast(Any, status), detail=detail[:500])
    return results


def usable_for_investigations(endpoint: ModelEndpoint) -> None:
    """Raise unless the endpoint's tool-calling test passed (design §13.2)."""
    result = endpoint.tests.get("tool_calling")
    if result is None:
        raise InvalidInput(
            f"model endpoint {endpoint.name!r} has not been tested; run its capability tests first"
        )
    if result.status != "passed":
        raise InvalidInput(
            f"model endpoint {endpoint.name!r} failed the tool-calling test ({result.detail}); "
            "investigations need tool calling"
        )
