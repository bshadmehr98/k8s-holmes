import pytest
from pydantic import ValidationError

from k8sholmes import records


def test_ids_have_prefixes_and_differ() -> None:
    a, b = records.new_id("inv"), records.new_id("inv")
    assert a.startswith("inv_")
    assert a != b


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        records.Environment.model_validate({"name": "a", "context": "c", "extra": 1})


def _finding(condition: str, resources: list[records.ResourceRef]) -> records.Finding:
    return records.Finding(
        investigation_id="i",
        environment_id="e",
        title="t",
        explanation="x",
        author="model:m",
        condition=condition,
        resources=resources,
    )


def test_finding_key_groups_same_condition_and_resources_only() -> None:
    r1 = records.ResourceRef(kind="Deployment", namespace="shop", name="api")
    r2 = records.ResourceRef(kind="Deployment", namespace="shop", name="web")
    a = _finding("replicas.single", [r1, r2])
    b = _finding("replicas.single", [r2, r1])
    c = _finding("limits.missing", [r1, r2])
    d = _finding("replicas.single", [r1])
    assert a.key() == b.key()
    assert a.key() != c.key()
    assert a.key() != d.key()


def test_mutable_defaults_are_not_shared() -> None:
    a = records.Scope()
    a.namespaces.append("x")
    assert records.Scope().namespaces == []
