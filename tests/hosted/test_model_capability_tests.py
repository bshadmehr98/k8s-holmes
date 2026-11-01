"""One real model through the capability tests (design §13.2, marker `hosted`).

Needs `K8SHOLMES_TEST_MODEL_URL` (an OpenAI-compatible base URL) and `K8SHOLMES_TEST_MODEL` (the model
name); `K8SHOLMES_TEST_MODEL_KEY` when the endpoint wants a key (a local llama.cpp or vLLM server does not).
"""

from __future__ import annotations

import os

import pytest

from k8sholmes.models import ANSWER_BUDGET, TESTS, OpenAICompatible, run_capability_tests
from k8sholmes.records import ModelEndpoint

pytestmark = pytest.mark.hosted


@pytest.fixture
def model() -> OpenAICompatible:
    url, name = os.environ.get("K8SHOLMES_TEST_MODEL_URL"), os.environ.get("K8SHOLMES_TEST_MODEL")
    if not url or not name:
        pytest.skip(
            "set K8SHOLMES_TEST_MODEL_URL and K8SHOLMES_TEST_MODEL (and K8SHOLMES_TEST_MODEL_KEY if needed)"
        )
    endpoint = ModelEndpoint(
        name="hosted test", base_url=url, model=name, api_key_env="K8SHOLMES_TEST_MODEL_KEY"
    )
    return OpenAICompatible(endpoint, os.environ.get("K8SHOLMES_TEST_MODEL_KEY") or None)


def test_the_capability_tests_tell_the_truth_about_a_real_endpoint(model: OpenAICompatible) -> None:
    results = run_capability_tests(model)
    key = os.environ.get("K8SHOLMES_TEST_MODEL_KEY")
    if key:  # checked first, so no later assertion message can print a detail holding the key
        assert all(key not in r.detail for r in results.values()), "a result's detail holds the key"
    assert set(results) == set(TESTS)
    assert all(r.status in ("passed", "failed", "unsupported") for r in results.values())
    assert results["connectivity"].status == "passed", results["connectivity"].detail
    assert results["authentication"].status == "passed", results["authentication"].detail
    assert results["model"].status == "passed", results["model"].detail
    # the gate on investigations follows the tool-calling result, whatever it is
    from k8sholmes.errors import InvalidInput
    from k8sholmes.models import usable_for_investigations

    tested = model.endpoint.model_copy(update={"tests": results})
    if results["tool_calling"].status == "passed":
        usable_for_investigations(tested)
    else:
        with pytest.raises(InvalidInput, match="tool-calling"):
            usable_for_investigations(tested)


def test_one_plain_completion_reports_usage(model: OpenAICompatible) -> None:
    reply = model.complete(
        [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=ANSWER_BUDGET
    )
    assert reply.content.strip()
    assert reply.completion_tokens >= 0 and reply.duration_ms >= 0
