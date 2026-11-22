"""Inputs a model can get right (change 0002, parts 2, 3 and 5): aliases, known values, teaching errors."""

from pathlib import Path

import pytest

from k8sholmes.capabilities.inputs import (
    MAX_ENUM,
    Known,
    canonical,
    explain,
    known_for,
    schema_for,
    unknown_values,
)
from k8sholmes.capabilities.kubernetes import InventoryInputs, ListInputs, LogsInputs, TopInputs
from k8sholmes.capabilities.reviews import ServiceInputs
from k8sholmes.records import Environment, Inventory, InventoryItem, ResourceRef
from k8sholmes.store import Store

KNOWN = Known.of(["pods", "services", "ingressroutes", "deployments"], {"shop", "kube-system"}, None)


def test_aliases_are_renamed_only_for_fields_the_model_has() -> None:
    assert canonical(ListInputs, {"kind": "svc", "labelSelector": "a=b", "fieldSelector": "x=y"}) == {
        "kind": "Service",
        "selector": "a=b",
        "field_selector": "x=y",
    }
    # LogsInputs has no `selector`: the alias stays and validation refuses it
    assert canonical(LogsInputs, {"pod": "p", "namespace": "n", "labels": "a"})["labels"] == "a"
    # a kind outside the table is left as sent (a CRD the cluster may serve)
    assert canonical(ListInputs, {"kind": "ingressroutes"})["kind"] == "ingressroutes"
    # TopInputs' kind is pods or nodes, not a resource kind: it is not rewritten
    assert canonical(TopInputs, {"kind": "pods"})["kind"] == "pods"


def test_known_kinds_list_the_table_first_and_accept_any_served_name() -> None:
    assert KNOWN.kind_choices() == ["Pod", "Deployment", "Service", "ingressroutes"]
    assert KNOWN.has_kind("po") and KNOWN.has_kind("Deployment") and KNOWN.has_kind("ingressroutes")
    assert not KNOWN.has_kind("Gadget")
    assert Known().has_kind("Gadget")  # nothing known: nothing is checked


def test_unknown_values_suggest_the_closest() -> None:
    problems = unknown_values(ListInputs, ListInputs(kind="Deploymnet", namespace="shp"), KNOWN)
    assert problems[0] == (
        "kind",
        "'Deploymnet' is not a kind this cluster serves; did you mean 'Deployment'?",
    )
    assert problems[1][0] == "namespace" and "did you mean 'shop'?" in problems[1][1]
    lists = unknown_values(InventoryInputs, InventoryInputs(namespaces=["shop", "nope"]), KNOWN)
    assert [p[0] for p in lists] == ["namespaces"]
    assert unknown_values(ListInputs, ListInputs(kind="Pod", namespace="x"), Known()) == []


def test_explain_lists_the_accepted_fields_and_suggests() -> None:
    text = explain("k8s.service_backends", ServiceInputs, [("nme", "Extra inputs are not permitted")])
    assert text.splitlines() == [
        "k8s.service_backends: invalid inputs",
        "- nme: Extra inputs are not permitted; did you mean 'name'?",
        "accepted fields: name (required), namespace (required)",
    ]
    aliased = explain("k8s.list", ListInputs, [("labels", "Extra inputs are not permitted")])
    assert "did you mean 'selector'?" in aliased


def test_schemas_carry_enums_within_the_allow_list_and_drop_them_when_too_many() -> None:
    plain = schema_for(ListInputs, None, [])
    assert "enum" not in plain["properties"]["kind"]
    schema = schema_for(ListInputs, KNOWN, ["shop", "elsewhere"])
    assert schema["properties"]["namespace"]["anyOf"][0]["enum"] == ["shop"]
    listed = schema_for(InventoryInputs, KNOWN, [])
    assert listed["properties"]["namespaces"]["items"] == {"type": "string", "enum": ["kube-system", "shop"]}
    required = schema_for(ServiceInputs, KNOWN, [])
    assert required["properties"]["namespace"]["enum"] == ["kube-system", "shop"]
    many = Known.of(["pods"], {f"ns-{i}" for i in range(MAX_ENUM + 1)}, None)
    assert "enum" not in str(schema_for(ServiceInputs, many, [])["properties"]["namespace"])


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "k.db")


def test_known_namespaces_come_from_the_latest_whole_inventory(store: Store) -> None:
    env = Environment(name="e", context="c", kinds=["pods"])
    store.put(env)
    assert known_for(store, env).namespaces == frozenset()

    def item(ns: str) -> InventoryItem:
        return InventoryItem(ref=ResourceRef(kind="Pod", namespace=ns, name="p"))

    store.put(Inventory(environment_id=env.id, items=[item("a"), item("b")]))
    store.put(Inventory(environment_id=env.id, namespaces=["a"], items=[item("a")]))  # scoped: not used
    known = known_for(store, env)
    assert known.namespaces == {"a", "b"} and known.kinds == {"pods"}
    assert known.inventory_at is not None
