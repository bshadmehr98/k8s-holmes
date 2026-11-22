"""Playbooks: code gathers, the model reasons (change 0002, part 6).

A playbook is a fixed list of capability calls for a common question shape. The model names one in its
framing (an enum of these ids or `none`); the engine runs its calls through the registry, so each one is
validated and judged by the gate like any other (design §5.2), and records them with the playbook's id.
The model's own gathering then continues with the results in its thread. A playbook adds no capability
and no permission. Its `limitation` is stated by code when the environment has no source that could
answer the part of the question the cluster cannot (§7.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from k8sholmes.inventory import WORKLOAD_KINDS
from k8sholmes.records import Framing, Inventory

MAX_CALLS = 40  # a fan-out over a large inventory stops here, with a limitation saying so

Plan = list[tuple[str, dict[str, Any]]]


@dataclass(frozen=True)
class Playbook:
    id: str  # stable, like a capability id
    title: str
    plan: Callable[[Framing, Inventory | None], Plan]
    needs: str = ""  # a system that could answer what the cluster cannot ("traffic"); empty: none
    limitation: str = ""  # stated when no available capability reaches `needs`


def _namespaces(framing: Framing, inventory: Inventory | None) -> list[str]:
    if framing.scope.namespaces:
        return list(framing.scope.namespaces)
    items = inventory.items if inventory else []
    return sorted({i.ref.namespace for i in items if i.ref.namespace})


def _unused(framing: Framing, inventory: Inventory | None) -> Plan:
    scope = list(framing.scope.namespaces)
    plan: Plan = [("k8s.inventory", {"namespaces": scope})]
    services = [i.ref for i in (inventory.items if inventory else []) if i.ref.kind == "Service"]
    plan += [
        ("k8s.service_backends", {"name": s.name, "namespace": s.namespace})
        for s in services
        if s.namespace and (not scope or s.namespace in scope)
    ]
    for kind in ("Deployment", "StatefulSet", "PersistentVolumeClaim"):
        plan += [("k8s.list", {"kind": kind, "namespace": ns}) for ns in scope or [None]]
    return plan


def _restarts(framing: Framing, inventory: Inventory | None) -> Plan:
    plan: Plan = [
        ("k8s.workload_health", {"kind": r.kind, "name": r.name, "namespace": r.namespace})
        for r in framing.scope.resources
        if r.kind in WORKLOAD_KINDS | {"Pod"} and r.namespace
    ]
    hours = _hours(framing)
    for ns in _namespaces(framing, inventory):
        plan += [
            ("k8s.events", {"namespace": ns, "since_hours": min(hours, 24 * 30)}),
            ("k8s.changes", {"namespace": ns, "since_hours": min(hours, 24 * 90)}),
        ]
    return plan


def _exposure(framing: Framing, inventory: Inventory | None) -> Plan:
    scope = list(framing.scope.namespaces)
    return [("k8s.network_exposure", {"namespaces": scope}), ("k8s.safeguards", {"namespaces": scope})]


def _what_changed(framing: Framing, inventory: Inventory | None) -> Plan:
    hours = _hours(framing)
    plan: Plan = []
    for ns in _namespaces(framing, inventory):
        plan += [
            ("k8s.changes", {"namespace": ns, "since_hours": min(hours, 24 * 90)}),
            ("k8s.events", {"namespace": ns, "since_hours": min(hours, 24 * 30)}),
        ]
    return plan


def _hours(framing: Framing) -> float:
    period = framing.period
    if period.start and period.end:
        return max(1.0, (period.end - period.start).total_seconds() / 3600)
    return 24.0


PLAYBOOKS: dict[str, Playbook] = {
    p.id: p
    for p in (
        Playbook(
            "unused",
            "Resources that look unused",
            _unused,
            needs="traffic",
            limitation=(
                "traffic: no connected source records requests, so whether anything is used cannot be "
                "answered (cannot_answer); the checks show only what nothing points at"
            ),
        ),
        Playbook("restarts", "Why workloads restart", _restarts),
        Playbook(
            "exposure",
            "What is exposed",
            _exposure,
            needs="network",
            limitation=(
                "beyond the cluster (cloud firewalls, a service mesh): cannot be answered from Kubernetes "
                "alone (cannot_answer)"
            ),
        ),
        Playbook("what_changed", "What changed", _what_changed),
    )
}


def plan_for(playbook: Playbook, framing: Framing, inventory: Inventory | None) -> tuple[Plan, list[str]]:
    """The calls to run, at most `MAX_CALLS`, and the limitations that cutting them adds."""
    plan = playbook.plan(framing, inventory)
    if len(plan) <= MAX_CALLS:
        return plan, []
    cut = f"the {playbook.id} playbook ran its first {MAX_CALLS} of {len(plan)} calls; narrow the scope"
    return plan[:MAX_CALLS], [cut]
