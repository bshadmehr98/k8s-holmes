"""Guarantee 20: every relationship is defined, observed or inferred, and an impact answer lists inferred dependents apart."""

from k8sholmes.records import Finding, Inventory
from tests.e2e.conftest import Demo


def test_inventory_relationships_and_the_impact_answer(demo: Demo) -> None:
    inv = demo.ask("What could be affected if api fails?")
    assert inv.status in ("completed", "partial"), inv.limitations
    inventory = demo.app.store.find(Inventory, {"environment_id": demo.env.id})[-1]
    assert {r.basis for r in inventory.relationships} == {"defined", "observed", "inferred"}
    impact = next(
        f
        for f in demo.app.store.find(Finding, {"investigation_id": inv.id})
        if f.condition == "custom.impact"
    )
    assert impact.kind == "hypothesis"
    text = impact.explanation
    assert "defined: Ingress/shop" in text
    assert "inferred: Deployment/scheduler, Deployment/web" in text
    assert "listed apart" in text
    assert inv.answer is not None
    assert "inferred:" in inv.answer and "defined:" in inv.answer
