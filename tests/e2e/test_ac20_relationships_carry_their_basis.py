"""Guarantee 20: every relationship is defined, observed or inferred, and inferred dependents are listed apart."""

from k8sholmes.records import Inventory, ResourceRef
from k8sholmes.relationships import dependents_of
from tests.e2e.conftest import Demo


def test_inventory_relationships_and_impact(demo: Demo) -> None:
    inv = demo.ask("What could be affected if api fails?")
    inventories = demo.app.store.find(Inventory, {"environment_id": demo.env.id})
    assert inventories
    inventory = inventories[-1]
    assert {r.basis for r in inventory.relationships} == {"defined", "observed", "inferred"}
    dependents = dependents_of(
        ResourceRef(kind="Service", namespace="shop", name="api"), inventory.relationships
    )
    assert any(
        r.source.name == "shop" and r.kind == "routes_to" for r in dependents["defined"]
    )  # the ingress
    assert {r.source.name for r in dependents["inferred"]} == {"web", "scheduler"}  # names in env and args
    assert inv.status in ("completed", "partial")
