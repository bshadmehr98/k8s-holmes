"""The OpenAI-compatible client against a small fake endpoint: requests, tool calls, streaming, failures."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from k8sholmes import records
from k8sholmes.errors import InvalidInput, ModelFailure
from k8sholmes.models import OpenAICompatible, price_of, run_capability_tests, usable_for_investigations
from tests.helpers import closed_port

SEEN: list[dict[str, Any]] = []
THINKING_TOKENS = 300  # what the fake reasoning model spends before its answer
MODE = {"value": "ok"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        SEEN.append({"path": self.path, "headers": dict(self.headers)})
        if self.headers.get("Authorization") != "Bearer sk-test-key":
            self._send(401, b'{"error": "unauthorized"}')
            return
        self._send(200, json.dumps({"data": [{"id": "test-model"}, {"id": "other"}]}).encode())

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        SEEN.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.headers.get("Authorization") != "Bearer sk-test-key":
            self._send(401, b'{"error": "unauthorized"}')
            return
        if not self._failure_or_thinking(body):
            self._answer(body)

    def _failure_or_thinking(self, body: dict[str, Any]) -> bool:
        """The failure modes, and a reasoning model whose budget runs out while it thinks."""
        failures = {
            "overloaded": (503, b'{"error": "overloaded"}'),
            "bad": (400, b'{"error": "bad request"}'),
            "garbage": (200, b"<html>not json</html>"),
        }
        if MODE["value"] in failures:
            self._send(*failures[MODE["value"]])
            return True
        if body.get("response_format") and MODE["value"] == "no_structured":
            self._send(400, b'{"error": "response_format is not supported"}')
            return True
        if MODE["value"] != "thinking" or body.get("max_tokens", 0) >= THINKING_TOKENS + 64:
            return False
        usage = {"prompt_tokens": 10, "completion_tokens": body["max_tokens"]}
        if body.get("stream"):
            chunk = json.dumps({"choices": [], "usage": usage})
            self._send(200, f"data: {chunk}\n\ndata: [DONE]\n\n".encode(), "text/event-stream")
            return True
        message = {"role": "assistant", "content": "", "reasoning": "Okay, the user wants..."}
        response = {"choices": [{"message": message, "finish_reason": "length"}], "usage": usage}
        self._send(200, json.dumps(response).encode())
        return True

    def _answer(self, body: dict[str, Any]) -> None:
        if body.get("stream"):
            chunks = [
                {"choices": [{"delta": {"content": "one "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "two"}, "finish_reason": None}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "get_time", "arguments": '{"ci'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Paris"}'}}]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}},
            ]
            payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            self._send(200, payload.encode(), "text/event-stream")
            return
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        prompt = body["messages"][-1]["content"]
        if body.get("tools") and "Paris" in prompt:
            message["tool_calls"] = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": '{"city": "Paris"}'},
                }
            ]
        elif body.get("response_format") or "JSON only" in prompt:
            message["content"] = '{"answer": 42}'
        elif "long story" in prompt:
            message["content"] = "The sea was"
        else:
            message["content"] = "ready"
        response = {
            "choices": [
                {"message": message, "finish_reason": "length" if "long story" in prompt else "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
        self._send(200, json.dumps(response).encode())


@pytest.fixture(scope="module")
def endpoint() -> Iterator[records.ModelEndpoint]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield records.ModelEndpoint(
        name="fake",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        model="test-model",
        price=records.ModelPrice(input_per_million=1.0, output_per_million=2.0),
    )
    server.shutdown()


@pytest.fixture(autouse=True)
def reset() -> None:
    SEEN.clear()
    MODE["value"] = "ok"


def test_completion_tool_calls_and_the_key_only_in_the_header(endpoint: records.ModelEndpoint) -> None:
    model = OpenAICompatible(endpoint, "sk-test-key")
    reply = model.complete([{"role": "user", "content": "hi"}], max_tokens=5)
    assert reply.content == "ready"
    assert (reply.prompt_tokens, reply.completion_tokens) == (10, 3)
    assert SEEN[-1]["body"]["model"] == "test-model"
    assert SEEN[-1]["body"]["max_tokens"] == 5
    assert "sk-test-key" not in json.dumps(SEEN[-1]["body"])
    tool = {
        "type": "function",
        "function": {"name": "get_time", "description": "", "parameters": {"type": "object"}},
    }
    reply = model.complete([{"role": "user", "content": "Time in Paris?"}], tools=[tool])
    assert reply.tool_calls[0].name == "get_time"
    assert reply.tool_calls[0].arguments == {"city": "Paris"}
    assert SEEN[-1]["body"]["tool_choice"] == "auto"


def test_streaming_assembles_content_and_tool_calls(endpoint: records.ModelEndpoint) -> None:
    model = OpenAICompatible(endpoint, "sk-test-key")
    deltas: list[str] = []
    reply = model.complete([{"role": "user", "content": "count"}], stream=True, on_delta=deltas.append)
    assert reply.content == "one two"
    assert deltas == ["one ", "two"]
    assert reply.tool_calls[0].arguments == {"city": "Paris"}
    assert reply.finish_reason == "tool_calls"
    assert reply.prompt_tokens == 7


def test_structured_replies_and_the_fallback(endpoint: records.ModelEndpoint) -> None:
    model = OpenAICompatible(endpoint, "sk-test-key")
    schema = {"title": "answer", "type": "object", "properties": {"answer": {"type": "integer"}}}
    reply = model.complete([{"role": "user", "content": "6*7?"}], schema=schema)
    assert json.loads(reply.content) == {"answer": 42}
    assert reply.structured_supported is True
    assert SEEN[-1]["body"]["response_format"]["json_schema"]["name"] == "answer"
    MODE["value"] = "no_structured"
    reply = model.complete([{"role": "user", "content": "6*7?"}], schema=schema)
    assert json.loads(reply.content) == {"answer": 42}
    assert reply.structured_supported is False
    assert "response_format" not in SEEN[-1]["body"]


def test_failures_are_typed_and_retryable_or_not(endpoint: records.ModelEndpoint) -> None:
    wrong_key = OpenAICompatible(endpoint, "nope")
    with pytest.raises(ModelFailure) as unauthorized:
        wrong_key.complete([{"role": "user", "content": "hi"}])
    assert unauthorized.value.status == 401
    assert not unauthorized.value.retryable
    model = OpenAICompatible(endpoint, "sk-test-key")
    MODE["value"] = "overloaded"
    with pytest.raises(ModelFailure) as overloaded:
        model.complete([{"role": "user", "content": "hi"}])
    assert overloaded.value.retryable
    MODE["value"] = "bad"
    with pytest.raises(ModelFailure) as bad:
        model.complete([{"role": "user", "content": "hi"}])
    assert not bad.value.retryable
    down = OpenAICompatible(
        endpoint.model_copy(
            update={
                "base_url": f"http://127.0.0.1:{closed_port()}/v1",
                "limits": records.ModelLimits(timeout_ms=500),
            }
        ),
        "k",
    )
    with pytest.raises(ModelFailure) as unreachable:
        down.complete([{"role": "user", "content": "hi"}])
    assert unreachable.value.retryable


def test_capability_tests_pass_on_a_good_endpoint_and_fail_honestly(endpoint: records.ModelEndpoint) -> None:
    results = run_capability_tests(OpenAICompatible(endpoint, "sk-test-key"))
    assert {k: v.status for k, v in results.items()} == {
        "connectivity": "passed",
        "authentication": "passed",
        "model": "passed",
        "tool_calling": "passed",
        "streaming": "passed",
        "structured": "passed",
        "limits": "passed",
    }
    assert "test-model among them" in results["connectivity"].detail
    results = run_capability_tests(OpenAICompatible(endpoint, "wrong"))
    assert results["connectivity"].status == "failed"
    assert "401" in results["connectivity"].detail
    assert results["tool_calling"].detail.startswith("not run")
    tested = endpoint.model_copy(update={"tests": results})
    with pytest.raises(InvalidInput, match="failed the tool-calling test"):
        usable_for_investigations(tested)
    with pytest.raises(InvalidInput, match="has not been tested"):
        usable_for_investigations(endpoint)
    assert price_of(endpoint, 1_000_000, 500_000) == 2.0
    assert price_of(endpoint.model_copy(update={"price": None}), 1, 1) is None


def test_capability_tests_leave_a_reasoning_model_room_to_think(endpoint: records.ModelEndpoint) -> None:
    """Reasoning models (qwen3, deepseek-r1, gpt-oss) think before they answer; a budget of a few tokens
    gives an empty reply and a false `failed` (found with qwen3:8b on Ollama)."""
    MODE["value"] = "thinking"
    results = run_capability_tests(OpenAICompatible(endpoint, "sk-test-key"))
    assert {k: v.status for k, v in results.items()} == {
        "connectivity": "passed",
        "authentication": "passed",
        "model": "passed",
        "tool_calling": "passed",
        "streaming": "passed",
        "structured": "passed",
        "limits": "passed",
    }


def test_a_non_json_body_is_a_typed_permanent_failure(endpoint: records.ModelEndpoint) -> None:
    MODE["value"] = "garbage"
    with pytest.raises(ModelFailure, match="not JSON") as failure:
        OpenAICompatible(endpoint, "sk-test-key").complete([{"role": "user", "content": "hi"}])
    assert not failure.value.retryable


def test_the_client_keeps_the_model_contract(endpoint: records.ModelEndpoint) -> None:
    from k8sholmes.testing import check_model

    check_model(OpenAICompatible(endpoint, "sk-test-key"))
