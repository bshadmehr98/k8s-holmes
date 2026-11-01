"""The built-in review capabilities: exposure, backends, safeguards, alert rules (design §5.3).

Each one computes a table by code from the cluster's records and states its coverage honestly: what the
cluster cannot know (traffic outside it, whether an alert fired) is `cannot_answer`, not a guess.
"""

from __future__ import annotations

import re
from typing import Any, cast

from pydantic import BaseModel, Field

from k8sholmes.capabilities import Capability, Context, Inputs
from k8sholmes.capabilities.kubernetes import INSPECT
from k8sholmes.environments import take_inventory
from k8sholmes.errors import NotFound
from k8sholmes.evidence import Result
from k8sholmes.inventory import WORKLOAD_KINDS, dig, item_of
from k8sholmes.records import Inventory, InventoryItem

OUTSIDE = (
    "anything outside the cluster (cloud firewalls, a mesh, DNS) is not inspected and cannot be answered"
)


class ScopeInputs(Inputs):
    namespaces: list[str] = Field(
        default=[], description="Namespaces to review; empty means every namespace the permissions allow."
    )


def _inventory(inputs: ScopeInputs, ctx: Context) -> Inventory:
    return take_inventory(
        ctx.store, ctx.environment, ctx.cluster, inputs.namespaces, max_items=ctx.settings.max_items
    )


def _by_kind(inventory: Inventory) -> dict[str, list[InventoryItem]]:
    out: dict[str, list[InventoryItem]] = {}
    for item in inventory.items:
        out.setdefault(item.ref.kind, []).append(item)
    return out


def _name(item: InventoryItem) -> str:
    return f"{item.ref.kind}/{item.ref.namespace}/{item.ref.name}"


# k8s.network_exposure


def run_network_exposure(inputs: BaseModel, ctx: Context) -> Result:
    inventory = _inventory(cast(ScopeInputs, inputs), ctx)
    by_kind = _by_kind(inventory)
    exposed = [
        {
            "service": _name(s),
            "type": s.summary.get("type"),
            "external_ips": s.summary.get("external_ips"),
            "load_balancer": s.summary.get("load_balancer"),
            "ports": s.summary.get("ports"),
        }
        for s in by_kind.get("Service", [])
        if s.summary.get("type") in ("LoadBalancer", "NodePort") or s.summary.get("external_ips")
    ]
    ingresses = [
        {
            "ingress": _name(i),
            "hosts": i.summary.get("hosts"),
            "tls": i.summary.get("tls"),
            "backends": i.summary.get("backends"),
        }
        for i in by_kind.get("Ingress", [])
    ]
    policies = [
        {
            "policy": _name(p),
            "selects_all": p.summary.get("selects_all"),
            "pod_selector": p.summary.get("pod_selector"),
            "policy_types": p.summary.get("policy_types"),
            "ingress_rules": p.summary.get("ingress_rules"),
            "egress_rules": p.summary.get("egress_rules"),
        }
        for p in by_kind.get("NetworkPolicy", [])
    ]
    selected = {r.target.key() for r in inventory.relationships if r.kind == "policy_selects"}
    unselected = [_name(p) for p in by_kind.get("Pod", []) if p.ref.key() not in selected]
    namespaces_with_policies = {p.ref.namespace for p in by_kind.get("NetworkPolicy", [])}
    namespaces = sorted({i.ref.namespace for i in inventory.items if i.ref.namespace})
    data = {
        "exposed_services": exposed,
        "ingresses": ingresses,
        "network_policies": policies,
        "pods_selected_by_no_policy": unselected,
        "namespaces_without_policies": [ns for ns in namespaces if ns not in namespaces_with_policies],
        "outside_cluster": {"coverage": "cannot_answer", "reason": OUTSIDE},
        "unreadable": inventory.unreadable,
    }
    summary = (
        f"{len(exposed)} exposed services, {len(ingresses)} ingresses, {len(policies)} network policies, "
        f"{len(unselected)} pods selected by no policy"
    )
    return Result(
        summary=summary, data=data, coverage="observed", coverage_reason=f"inside the cluster only; {OUTSIDE}"
    )


# k8s.service_backends


class ServiceInputs(Inputs):
    name: str
    namespace: str


def run_service_backends(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ServiceInputs, inputs)
    try:
        service = item_of(ctx.cluster.get("service", i.name, namespace=i.namespace))
    except NotFound as exc:
        return Result(
            summary=str(exc), data=None, coverage="observed_absent", coverage_reason="no such service"
        )
    selector = cast(dict[str, str], service.summary.get("selector") or {})
    pods = (
        [
            item_of(p)
            for p in ctx.cluster.list(
                "pods", namespace=i.namespace, selector=",".join(f"{k}={v}" for k, v in selector.items())
            )
        ]
        if selector
        else []
    )
    slices = [
        item_of(s)
        for s in ctx.cluster.list(
            "endpointslices", namespace=i.namespace, selector=f"kubernetes.io/service-name={i.name}"
        )
    ]
    ready_endpoints = sum(
        1
        for s in slices
        for e in cast(list[dict[str, Any]], s.summary.get("endpoints") or [])
        if e.get("ready")
    )
    container_ports = {
        p
        for pod in pods
        for c in cast(list[dict[str, Any]], pod.summary.get("containers") or [])
        for p in cast(list[Any], c.get("ports") or [])
    }
    port_alignment: list[dict[str, Any]] = []
    for port in cast(list[dict[str, Any]], service.summary.get("ports") or []):
        target = port.get("targetPort", port.get("port"))
        matches = isinstance(target, str) or target in container_ports or not pods
        port_alignment.append(
            {"port": port.get("port"), "targetPort": target, "matches_a_container_port": matches}
        )
    data = {
        "service": {"ref": service.ref.model_dump(), "summary": service.summary},
        "selector": selector,
        "matching_pods": [
            {
                "ref": p.ref.model_dump(),
                "ready": p.summary.get("ready"),
                "phase": p.summary.get("phase"),
                "labels": p.labels,
            }
            for p in pods
        ],
        "ready_endpoints": ready_endpoints,
        "endpoint_slices": [s.summary for s in slices],
        "ports": port_alignment,
        "traffic": {
            "coverage": "cannot_answer",
            "reason": (
                "the cluster records no request traffic; whether anyone calls this service needs a metrics "
                "or proxy source"
            ),
        },
    }
    if not selector:
        coverage, reason = "observed", "the service has no selector (ExternalName or manual endpoints)"
    elif not pods:
        coverage, reason = "observed_absent", "the selector matches no pod"
    else:
        coverage, reason = "observed", f"{len(pods)} pods match, {ready_endpoints} ready endpoints"
    summary = f"service {i.name}: {len(pods)} matching pods, {ready_endpoints} ready endpoints"
    return Result(summary=summary, data=data, coverage=coverage, coverage_reason=reason)  # type: ignore[arg-type]


# k8s.safeguards


def _workload_row(item: InventoryItem, inventory: Inventory) -> dict[str, Any]:
    containers = cast(list[dict[str, Any]], item.summary.get("containers") or [])
    targeted_by = [
        r.source for r in inventory.relationships if r.target.key() == item.ref.key() and r.kind == "targets"
    ]
    return {
        "workload": _name(item),
        "kind": item.ref.kind,
        "replicas": item.summary.get("replicas"),
        "liveness_probe": all(c.get("liveness") for c in containers) if containers else False,
        "readiness_probe": all(c.get("readiness") for c in containers) if containers else False,
        "requests": all(c.get("requests") for c in containers) if containers else False,
        "limits": all(c.get("limits") for c in containers) if containers else False,
        "pdb": any(r.kind == "PodDisruptionBudget" for r in targeted_by),
        "hpa": any(r.kind == "HorizontalPodAutoscaler" for r in targeted_by),
        "anti_affinity": bool(item.summary.get("anti_affinity")),
        "latest_tag": any(
            str(c.get("image", "")).endswith(":latest")
            or ":" not in str(c.get("image", "")).rsplit("/", 1)[-1]
            for c in containers
        ),
        "automount_token": item.summary.get("automount_token") is not False,
        "images": [c.get("image") for c in containers],
    }


def run_safeguards(inputs: BaseModel, ctx: Context) -> Result:
    inventory = _inventory(cast(ScopeInputs, inputs), ctx)
    rows = [
        _workload_row(item, inventory)
        for item in inventory.items
        if item.ref.kind in ("Deployment", "StatefulSet", "DaemonSet")
    ]
    counts = {
        "workloads": len(rows),
        "without_liveness_probe": sum(1 for r in rows if not r["liveness_probe"]),
        "without_readiness_probe": sum(1 for r in rows if not r["readiness_probe"]),
        "without_limits": sum(1 for r in rows if not r["limits"]),
        "without_requests": sum(1 for r in rows if not r["requests"]),
        "single_replica": sum(1 for r in rows if r["kind"] != "DaemonSet" and (r["replicas"] or 0) <= 1),
        "without_pdb": sum(1 for r in rows if r["kind"] != "DaemonSet" and not r["pdb"]),
        "latest_tag": sum(1 for r in rows if r["latest_tag"]),
    }
    summary = (
        f"{counts['workloads']} workloads: {counts['without_liveness_probe']} without liveness probes, "
        f"{counts['without_limits']} without limits, {counts['single_replica']} single replica, "
        f"{counts['without_pdb']} without a PDB"
    )
    return Result(
        summary=summary,
        data={"workloads": rows, "counts": counts, "unreadable": inventory.unreadable},
        coverage="observed",
        coverage_reason="from the workloads' definitions; these are safeguards, not evidence of incidents",
    )


# k8s.alert_rules


def _ns_name(resource: dict[str, Any]) -> str:
    return f"{dig(resource, 'metadata', 'namespace')}/{dig(resource, 'metadata', 'name')}"


def _rules_of(rule_resource: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group in cast(list[dict[str, Any]], dig(rule_resource, "spec", "groups", default=[]) or []):
        for rule in cast(list[dict[str, Any]], group.get("rules", []) or []):
            if rule.get("alert"):
                out.append(
                    {
                        "alert": rule.get("alert"),
                        "expr": rule.get("expr"),
                        "for": rule.get("for"),
                        "labels": rule.get("labels", {}),
                        "group": group.get("name"),
                    }
                )
    return out


def run_alert_rules(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ScopeInputs, inputs)
    kinds = {k.split(".")[0] for k in ctx.cluster.api_resources()}
    if "prometheusrules" not in kinds:
        return Result(
            summary="no alert rule source",
            data=None,
            coverage="cannot_answer",
            coverage_reason=(
                "the PrometheusRule CRD is not installed; which alerts exist or fired cannot be read from "
                "this cluster"
            ),
        )
    namespaces = i.namespaces or [None]
    rules: list[dict[str, Any]] = []
    monitors: list[dict[str, Any]] = []
    for ns in namespaces:
        for r in ctx.cluster.list("prometheusrules", namespace=ns):
            for rule in _rules_of(r):
                rules.append(
                    {
                        **rule,
                        "source": f"PrometheusRule/{_ns_name(r)}",
                    }
                )
        if "servicemonitors" in kinds:
            monitors.extend(
                {
                    "monitor": f"ServiceMonitor/{_ns_name(m)}",
                    "selector": dig(m, "spec", "selector", "matchLabels", default={}),
                }
                for m in ctx.cluster.list("servicemonitors", namespace=ns)
            )
    inventory = _inventory(i, ctx)
    workloads = [item for item in inventory.items if item.ref.kind in WORKLOAD_KINDS]
    referenced: dict[str, list[str]] = {}
    for wl in workloads:
        pattern = re.compile(rf"\b{re.escape(wl.ref.name)}\b")
        referenced[_name(wl)] = [
            str(r["alert"])
            for r in rules
            if pattern.search(str(r.get("expr") or ""))
            or any(wl.ref.name in str(v) for v in cast(dict[str, Any], r.get("labels") or {}).values())
        ]
    without = [name for name, alerts in referenced.items() if not alerts]
    data = {
        "rules": rules,
        "service_monitors": monitors,
        "workloads_referenced_by_alerts": referenced,
        "workloads_without_alerts": without,
        "fired": {
            "coverage": "cannot_answer",
            "reason": "rules are read from the cluster; whether an alert fired needs the alert manager",
        },
    }
    summary = (
        f"{len(rules)} alert rules, {len(monitors)} service monitors; "
        f"{len(without)} of {len(workloads)} workloads referenced by no alert"
    )
    return Result(
        summary=summary,
        data=data,
        coverage="observed",
        coverage_reason="rules as defined in PrometheusRule objects; firing is cannot_answer",
    )


CAPABILITIES: list[Capability] = [
    INSPECT(
        id="k8s.network_exposure",
        title="Network exposure",
        inputs=ScopeInputs,
        run=run_network_exposure,
        permissions=(
            ("list", "services"),
            ("list", "ingresses"),
            ("list", "networkpolicies"),
            ("list", "pods"),
        ),
        coverage=("observed", "cannot_answer"),
        purpose=(
            "Services of type LoadBalancer or NodePort, Ingresses and their hosts, NetworkPolicies and which "
            "pods they select, pods selected by none, namespaces without policies. Only what the cluster "
            "shows; anything beyond it is cannot_answer."
        ),
    ),
    INSPECT(
        id="k8s.service_backends",
        title="A service's backends",
        inputs=ServiceInputs,
        run=run_service_backends,
        permissions=(("get", "pods"), ("list", "pods"), ("list", "endpointslices")),
        coverage=("observed", "observed_absent", "cannot_answer"),
        purpose=(
            "A Service's selector, the pods it matches and their readiness, its endpoint slices, and whether "
            "its target ports match a container port. Traffic is cannot_answer."
        ),
    ),
    INSPECT(
        id="k8s.safeguards",
        title="Reliability safeguards",
        inputs=ScopeInputs,
        run=run_safeguards,
        permissions=(
            ("list", "deployments"),
            ("list", "statefulsets"),
            ("list", "daemonsets"),
            ("list", "poddisruptionbudgets"),
            ("list", "horizontalpodautoscalers"),
        ),
        coverage=("observed",),
        purpose=(
            "Per workload: liveness and readiness probes, requests and limits, replicas, a "
            "PodDisruptionBudget, an autoscaler, anti-affinity, :latest tags, token automount; with counts "
            "computed by code. The basis of a reliability review."
        ),
    ),
    INSPECT(
        id="k8s.alert_rules",
        title="Alert rules",
        inputs=ScopeInputs,
        run=run_alert_rules,
        permissions=(("list", "prometheusrules"), ("list", "servicemonitors")),
        coverage=("observed", "cannot_answer"),
        purpose=(
            "PrometheusRule alerts and ServiceMonitors in the scope when those CRDs exist, and which "
            "workloads no alert references. Whether an alert fired is cannot_answer; without the CRDs "
            "everything is."
        ),
    ),
]
