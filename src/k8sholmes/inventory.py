"""Inventory items: the summary each kind keeps (design §4.2). Plain functions over kubectl's JSON."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from k8sholmes.records import InventoryItem, ResourceRef

# the kinds an inventory reads, in the order they are listed (design §4.2)
INVENTORY_KINDS: tuple[str, ...] = (
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "Pod",
    "ReplicaSet",
    "Service",
    "EndpointSlice",
    "Ingress",
    "NetworkPolicy",
    "PersistentVolumeClaim",
    "HorizontalPodAutoscaler",
    "PodDisruptionBudget",
    "ServiceAccount",
    "ConfigMap",
    "Secret",
)
WORKLOAD_KINDS: frozenset[str] = frozenset({"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"})


def get(obj: Any, *path: str, default: Any = None) -> Any:
    value: Any = obj
    for part in path:
        if not isinstance(value, dict):
            return default
        value = cast(dict[str, Any], value).get(part)
    return default if value is None else value


def ref_of(resource: dict[str, Any]) -> ResourceRef:
    meta = cast(dict[str, Any], resource.get("metadata", {}))
    return ResourceRef(
        kind=str(resource.get("kind", "")), namespace=meta.get("namespace"), name=str(meta.get("name", ""))
    )


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _containers(pod_spec: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in cast(list[dict[str, Any]], pod_spec.get("containers", []) or []):
        resources = cast(dict[str, Any], c.get("resources", {}) or {})
        out.append(
            {
                "name": c.get("name"),
                "image": c.get("image"),
                "ports": [
                    p.get("containerPort") for p in cast(list[dict[str, Any]], c.get("ports", []) or [])
                ],
                "requests": resources.get("requests") or {},
                "limits": resources.get("limits") or {},
                "liveness": "livenessProbe" in c,
                "readiness": "readinessProbe" in c,
                "startup": "startupProbe" in c,
                "probes": {k: c[k] for k in ("livenessProbe", "readinessProbe", "startupProbe") if k in c},
                "env": c.get("env", []) or [],
                "envFrom": c.get("envFrom", []) or [],
                "args": (c.get("args", []) or []) + (c.get("command", []) or []),
            }
        )
    return out


def _pod_template(resource: dict[str, Any]) -> dict[str, Any]:
    kind = resource.get("kind")
    if kind == "CronJob":
        return cast(
            dict[str, Any], get(resource, "spec", "jobTemplate", "spec", "template", "spec", default={})
        )
    if kind == "Pod":
        return cast(dict[str, Any], resource.get("spec", {}) or {})
    return cast(dict[str, Any], get(resource, "spec", "template", "spec", default={}))


def _pod_summary(resource: dict[str, Any]) -> dict[str, Any]:
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    statuses = cast(list[dict[str, Any]], status.get("containerStatuses", []) or [])
    conditions = cast(list[dict[str, Any]], status.get("conditions", []) or [])
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
    return {
        "phase": status.get("phase"),
        "ready": ready,
        "node": get(resource, "spec", "nodeName"),
        "restarts": sum(int(s.get("restartCount", 0) or 0) for s in statuses),
        "container_states": [
            {
                "name": s.get("name"),
                "ready": s.get("ready"),
                "restarts": s.get("restartCount", 0),
                "waiting": get(s, "state", "waiting", "reason"),
                "waiting_message": get(s, "state", "waiting", "message"),
                "last_terminated": get(s, "lastState", "terminated", "reason"),
                "last_exit_code": get(s, "lastState", "terminated", "exitCode"),
                "last_finished": get(s, "lastState", "terminated", "finishedAt"),
            }
            for s in statuses
        ],
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")} for c in conditions
        ],
        "containers": _containers(_pod_template(resource)),
        "service_account": get(resource, "spec", "serviceAccountName"),
        "automount_token": get(resource, "spec", "automountServiceAccountToken"),
        "volumes": resource.get("spec", {}).get("volumes", []) or [],
        "deletion_timestamp": get(resource, "metadata", "deletionTimestamp"),
    }


def _workload_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    template = _pod_template(resource)
    return {
        "replicas": spec.get(
            "replicas", 1 if resource.get("kind") != "DaemonSet" else status.get("desiredNumberScheduled")
        ),
        "ready": status.get("readyReplicas", status.get("numberReady")),
        "available": status.get("availableReplicas", status.get("numberAvailable")),
        "updated": status.get("updatedReplicas"),
        "generation": get(resource, "metadata", "generation"),
        "observed_generation": status.get("observedGeneration"),
        "conditions": [
            {
                "type": c.get("type"),
                "status": c.get("status"),
                "reason": c.get("reason"),
                "message": c.get("message"),
            }
            for c in cast(list[dict[str, Any]], status.get("conditions", []) or [])
        ],
        "selector": get(spec, "selector", "matchLabels", default={}),
        "strategy": get(spec, "strategy", "type") or get(spec, "updateStrategy", "type"),
        "containers": _containers(template),
        "pod_labels": get(spec, "template", "metadata", "labels", default={}),
        "anti_affinity": bool(get(template, "affinity", "podAntiAffinity")),
        "service_account": template.get("serviceAccountName"),
        "automount_token": template.get("automountServiceAccountToken"),
        "volumes": template.get("volumes", []) or [],
        "schedule": spec.get("schedule"),
        "suspend": spec.get("suspend"),
        "last_schedule": status.get("lastScheduleTime"),
        "completions": spec.get("completions"),
        "succeeded": status.get("succeeded"),
        "failed": status.get("failed"),
        "revision": get(resource, "metadata", "annotations", "deployment.kubernetes.io/revision"),
        "change_cause": get(resource, "metadata", "annotations", "kubernetes.io/change-cause"),
    }


def _service_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    return {
        "type": spec.get("type", "ClusterIP"),
        "selector": spec.get("selector") or {},
        "ports": [
            {
                "port": p.get("port"),
                "targetPort": p.get("targetPort"),
                "nodePort": p.get("nodePort"),
                "protocol": p.get("protocol"),
            }
            for p in cast(list[dict[str, Any]], spec.get("ports", []) or [])
        ],
        "cluster_ip": spec.get("clusterIP"),
        "external_ips": spec.get("externalIPs", []) or [],
        "load_balancer": get(resource, "status", "loadBalancer", "ingress", default=[]),
    }


def _ingress_summary(resource: dict[str, Any]) -> dict[str, Any]:
    rules = cast(list[dict[str, Any]], get(resource, "spec", "rules", default=[]) or [])
    backends: list[dict[str, Any]] = []
    for rule in rules:
        for path in cast(list[dict[str, Any]], get(rule, "http", "paths", default=[]) or []):
            service = cast(dict[str, Any], get(path, "backend", "service", default={}) or {})
            backends.append(
                {
                    "host": rule.get("host"),
                    "path": path.get("path"),
                    "service": service.get("name"),
                    "port": get(service, "port", "number") or get(service, "port", "name"),
                }
            )
    default = cast(dict[str, Any], get(resource, "spec", "defaultBackend", "service", default={}) or {})
    if default:
        backends.append(
            {
                "host": None,
                "path": None,
                "service": default.get("name"),
                "port": get(default, "port", "number"),
            }
        )
    return {
        "hosts": [r.get("host") for r in rules if r.get("host")],
        "backends": backends,
        "tls": bool(get(resource, "spec", "tls")),
        "class": get(resource, "spec", "ingressClassName"),
    }


def _network_policy_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    return {
        "pod_selector": get(spec, "podSelector", "matchLabels", default={}),
        "selects_all": spec.get("podSelector", {}) == {} or spec.get("podSelector") == {"matchLabels": {}},
        "policy_types": spec.get("policyTypes", []) or [],
        "ingress_rules": len(spec.get("ingress", []) or []),
        "egress_rules": len(spec.get("egress", []) or []),
    }


def _conditions(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": c.get("type"),
            "status": c.get("status"),
            "reason": c.get("reason"),
            "message": c.get("message"),
        }
        for c in cast(list[dict[str, Any]], status.get("conditions", []) or [])
    ]


def _pvc_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    return {
        "phase": status.get("phase"),
        "storage_class": spec.get("storageClassName"),
        "capacity": get(status, "capacity", "storage"),
        "volume": spec.get("volumeName"),
    }


def _hpa_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    return {
        "target": get(spec, "scaleTargetRef", default={}),
        "min": spec.get("minReplicas"),
        "max": spec.get("maxReplicas"),
        "current": status.get("currentReplicas"),
        "desired": status.get("desiredReplicas"),
        "conditions": _conditions(status),
    }


def _pdb_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    return {
        "selector": get(spec, "selector", "matchLabels", default={}),
        "min_available": spec.get("minAvailable"),
        "max_unavailable": spec.get("maxUnavailable"),
        "disruptions_allowed": status.get("disruptionsAllowed"),
    }


def _endpoint_slice_summary(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "service": get(resource, "metadata", "labels", "kubernetes.io/service-name"),
        "endpoints": [
            {
                "target": e.get("targetRef", {}),
                "ready": get(e, "conditions", "ready"),
                "addresses": e.get("addresses", []),
            }
            for e in cast(list[dict[str, Any]], resource.get("endpoints", []) or [])
        ],
        "ports": resource.get("ports", []) or [],
    }


def _config_map_summary(resource: dict[str, Any]) -> dict[str, Any]:
    return {"keys": sorted(cast(dict[str, Any], resource.get("data", {}) or {}).keys())}


def _secret_summary(resource: dict[str, Any]) -> dict[str, Any]:
    return {"type": resource.get("type"), "body": "never fetched"}


def _service_account_summary(resource: dict[str, Any]) -> dict[str, Any]:
    pull = cast(list[dict[str, Any]], resource.get("imagePullSecrets", []) or [])
    return {
        "automount_token": resource.get("automountServiceAccountToken"),
        "image_pull_secrets": [s.get("name") for s in pull],
    }


def _node_summary(resource: dict[str, Any]) -> dict[str, Any]:
    spec = cast(dict[str, Any], resource.get("spec", {}) or {})
    status = cast(dict[str, Any], resource.get("status", {}) or {})
    return {
        "conditions": _conditions(status),
        "allocatable": status.get("allocatable", {}),
        "taints": spec.get("taints", []) or [],
        "unschedulable": spec.get("unschedulable", False),
    }


SUMMARIES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "Pod": _pod_summary,
    "Deployment": _workload_summary,
    "StatefulSet": _workload_summary,
    "DaemonSet": _workload_summary,
    "ReplicaSet": _workload_summary,
    "Job": _workload_summary,
    "CronJob": _workload_summary,
    "Service": _service_summary,
    "Ingress": _ingress_summary,
    "NetworkPolicy": _network_policy_summary,
    "PersistentVolumeClaim": _pvc_summary,
    "HorizontalPodAutoscaler": _hpa_summary,
    "PodDisruptionBudget": _pdb_summary,
    "EndpointSlice": _endpoint_slice_summary,
    "ConfigMap": _config_map_summary,
    "Secret": _secret_summary,
    "ServiceAccount": _service_account_summary,
    "Node": _node_summary,
}


def item_of(resource: dict[str, Any]) -> InventoryItem:
    """The inventory item of one resource: reference, labels, owner, age and the summary its kind keeps."""
    meta = cast(dict[str, Any], resource.get("metadata", {}) or {})
    owners = cast(list[dict[str, Any]], meta.get("ownerReferences", []) or [])
    owner = None
    if owners:
        owner = ResourceRef(
            kind=str(owners[0].get("kind", "")),
            namespace=meta.get("namespace"),
            name=str(owners[0].get("name", "")),
        )
    summarize = SUMMARIES.get(str(resource.get("kind", "")))
    summary = summarize(resource) if summarize else {}
    return InventoryItem(
        ref=ref_of(resource),
        labels=dict(cast(dict[str, str], meta.get("labels", {}) or {})),
        owner=owner,
        created_at=parse_time(meta.get("creationTimestamp")),
        summary=summary,
    )
