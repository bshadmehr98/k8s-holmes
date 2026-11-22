"""Playbook plans (change 0002, part 6): what each one asks for, and the cap on a fan-out."""

from datetime import timedelta

from k8sholmes.playbooks import MAX_CALLS, PLAYBOOKS, plan_for
from k8sholmes.records import Framing, Inventory, InventoryItem, Period, ResourceRef, Scope, now


def inventory(*refs: tuple[str, str, str]) -> Inventory:
    return Inventory(
        environment_id="e",
        items=[InventoryItem(ref=ResourceRef(kind=k, namespace=ns, name=n)) for k, ns, n in refs],
    )


def test_restarts_reads_the_named_workloads_and_each_namespace_in_the_period() -> None:
    end = now()
    framing = Framing(
        question="q",
        scope=Scope(
            resources=[
                ResourceRef(kind="Deployment", namespace="shop", name="api"),
                ResourceRef(kind="Service", namespace="shop", name="api"),
            ]
        ),
        period=Period(start=end - timedelta(hours=48), end=end),
    )
    plan, cut = plan_for(PLAYBOOKS["restarts"], framing, inventory(("Pod", "shop", "a"), ("Pod", "ops", "b")))
    assert cut == []
    assert plan[0] == ("k8s.workload_health", {"kind": "Deployment", "name": "api", "namespace": "shop"})
    assert ("k8s.events", {"namespace": "ops", "since_hours": 48.0}) in plan
    assert ("k8s.changes", {"namespace": "shop", "since_hours": 48.0}) in plan
    assert not any(
        name == "k8s.workload_health" and args["name"] == "api" and args["kind"] == "Service"
        for name, args in plan
    )


def test_a_fan_out_is_capped_with_a_limitation_saying_so() -> None:
    services = [("Service", "shop", f"s{i}") for i in range(MAX_CALLS + 5)]
    framing = Framing(question="q", scope=Scope(namespaces=["shop"]))
    plan, cut = plan_for(PLAYBOOKS["unused"], framing, inventory(*services))
    assert len(plan) == MAX_CALLS
    assert cut == [
        f"the unused playbook ran its first {MAX_CALLS} of {MAX_CALLS + 5 + 4} calls; narrow the scope"
    ]


def test_unused_stays_in_the_scope_and_exposure_reviews_it() -> None:
    framing = Framing(question="q", scope=Scope(namespaces=["shop"]))
    plan, _ = plan_for(
        PLAYBOOKS["unused"], framing, inventory(("Service", "shop", "a"), ("Service", "ops", "b"))
    )
    assert ("k8s.service_backends", {"name": "a", "namespace": "shop"}) in plan
    assert all(args.get("namespace") != "ops" for _, args in plan)
    exposure, _ = plan_for(PLAYBOOKS["exposure"], framing, None)
    assert [name for name, _ in exposure] == ["k8s.network_exposure", "k8s.safeguards"]


def test_periods_are_capped_and_default_to_a_day_and_an_empty_scope_reads_every_namespace() -> None:
    end = now()
    long = Framing(question="q", period=Period(start=end - timedelta(days=200), end=end))
    plan, _ = plan_for(PLAYBOOKS["what_changed"], long, inventory(("Pod", "shop", "a")))
    assert plan == [
        ("k8s.changes", {"namespace": "shop", "since_hours": 24 * 90}),
        ("k8s.events", {"namespace": "shop", "since_hours": 24 * 30}),
    ]
    plain, _ = plan_for(PLAYBOOKS["restarts"], Framing(question="q"), inventory(("Pod", "ops", "a")))
    assert ("k8s.events", {"namespace": "ops", "since_hours": 24.0}) in plain
    unused, _ = plan_for(PLAYBOOKS["unused"], Framing(question="q"), None)
    assert ("k8s.list", {"kind": "Deployment", "namespace": None}) in unused


def test_a_playbook_named_in_any_other_shape_is_none() -> None:
    from k8sholmes.records import Investigation
    from k8sholmes.thread import framing_from

    inv = Investigation(environment_id="e", question="q", created_by="u")
    assert framing_from({"playbook": "unused"}, inv).playbook == "unused"
    for odd in (["unused"], {"id": "unused"}, "bogus", None, 3):
        assert framing_from({"playbook": odd}, inv).playbook is None
