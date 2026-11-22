"""What a model sees of a result: `shrink` keeps the shape and the head of a large result."""

import json
from typing import Any

from k8sholmes.thread import shrink


def _pod(n: int) -> dict[str, Any]:
    return {
        "ref": {"kind": "Pod", "namespace": "shop", "name": f"api-{n}"},
        "summary": {"container_states": [{"last_terminated": "OOMKilled"}], "restarts": n},
        "labels": {f"label-{i}": "x" * 40 for i in range(30)},
    }


def test_a_small_result_is_unchanged() -> None:
    data = {"pods": [_pod(1)], "workload": {"name": "api"}}
    assert shrink(data, 12_000) == data


def test_the_largest_value_is_cut_not_dropped() -> None:
    """A Deployment mid-rollout on a real cluster: the pods list is the largest value. The model must still
    see the first pods with their states, not a note that the whole list was omitted."""
    data = {"workload": {"name": "api"}, "pods": [_pod(n) for n in range(6)], "events": ["e"] * 3}
    out = shrink(data, 6_000)
    assert len(json.dumps(out)) <= 6_000
    assert out["workload"] == {"name": "api"} and out["events"] == ["e"] * 3
    pods = out["pods"]
    assert isinstance(pods, list) and isinstance(pods[0], dict)
    assert pods[0]["summary"]["container_states"] == [{"last_terminated": "OOMKilled"}]
    assert isinstance(pods[-1], str) and "omitted" in pods[-1]


def test_a_single_huge_item_is_cut_inside() -> None:
    data = {"pods": [{"summary": {"restarts": 2}, "log": "y" * 50_000}]}
    out = shrink(data, 2_000)
    assert len(json.dumps(out)) <= 2_000
    assert out["pods"][0]["summary"] == {"restarts": 2}


def test_shrink_always_fits_and_ends() -> None:
    data = {f"k{i}": "z" * 500 for i in range(40)}
    out = shrink(data, 300)
    assert len(json.dumps(out)) <= 300 or all(isinstance(v, str) and v.startswith("<") for v in out.values())
    assert shrink("w" * 1_000, 100).endswith("stored as evidence]")


def test_fit_compacts_the_oldest_results_wherever_they_sit_and_never_the_newest_four() -> None:
    """Change 0002, part 7: positions come from the messages, so an inserted reply cannot shift them."""
    import json as _json

    from k8sholmes.thread import fit, tokens_of

    def result(n: int, role: str = "tool") -> dict:
        body = _json.dumps(
            {"evidence_id": f"ev_{n}", "status": "completed", "summary": "s", "data": "x" * 2000}
        )
        return {"role": role, "content": body if role == "tool" else f"Result of k8s.list (data):\n{body}"}

    messages = [{"role": "system", "content": "p"}, {"role": "user", "content": "q"}]
    messages += [result(1), {"role": "tool", "content": '{"error": "not run: interrupted"}'}]
    messages += [result(n, "user" if n % 2 else "tool") for n in range(2, 8)]
    assert fit(messages, None) is messages  # no limit: untouched
    fitted = fit(messages, 1)  # cannot fit: every older result is compacted, the newest four stay whole
    shortened = [m for m in fitted if "an older result" in str(m["content"])]
    assert len(shortened) == 3 and all(
        _json.loads(m["content"].rpartition("\n")[2])["evidence_id"] for m in shortened
    )
    assert all("an older result" not in str(m["content"]) for m in fitted[-4:])
    assert fitted[5]["content"].startswith("Result of k8s.list (data):\n")  # the header stays
    assert "x" * 2000 in messages[2]["content"]  # a copy: the input is unchanged
    roomy = fit(messages, tokens_of(messages) - 100)
    assert sum("an older result" in str(m["content"]) for m in roomy) == 1  # only as much as needed
