"""Environments: connecting, the permission matrix, the inventory snapshot (design §4).

An environment is one kubeconfig context (or a recording). Connecting asks the cluster what it may read
and keeps the answers on the environment as the permission matrix; the inventory lists the kinds the
matrix allows and builds the relationships by code.
"""

from __future__ import annotations

from typing import Any

from k8sholmes.cluster import KINDS, Cluster, Kubectl, canonical_kind
from k8sholmes.errors import ClusterUnreachable, InvalidInput, NotFound, PermissionRefused
from k8sholmes.inventory import INVENTORY_KINDS, get, item_of
from k8sholmes.records import Environment, Inventory, InventoryItem, now
from k8sholmes.relationships import build_relationships
from k8sholmes.store import Store
from k8sholmes.testing.cluster import RecordedCluster

# (verb, kind) pairs the built-in capabilities need (design §4.1); checked cluster-wide on connect
PERMISSION_CHECKS: tuple[tuple[str, str], ...] = (
    ("list", "pods"),
    ("get", "pods"),
    ("get", "pods/log"),
    ("list", "events"),
    ("list", "deployments"),
    ("list", "replicasets"),
    ("list", "statefulsets"),
    ("list", "daemonsets"),
    ("list", "jobs"),
    ("list", "cronjobs"),
    ("list", "services"),
    ("list", "endpointslices"),
    ("list", "ingresses"),
    ("list", "networkpolicies"),
    ("list", "persistentvolumeclaims"),
    ("list", "horizontalpodautoscalers"),
    ("list", "poddisruptionbudgets"),
    ("list", "serviceaccounts"),
    ("list", "configmaps"),
    ("list", "secrets"),
    ("list", "nodes"),
    ("list", "namespaces"),
    ("list", "prometheusrules"),
    ("list", "servicemonitors"),
    ("list", "pods.metrics.k8s.io"),
    ("list", "nodes.metrics.k8s.io"),
)
PLURALS: dict[str, str] = {
    kind: plural for plural, kind in KINDS.items() if plural.endswith("s") and not plural.endswith("ss")
}
PLURALS.update(
    {
        "Ingress": "ingresses",
        "NetworkPolicy": "networkpolicies",
        "StorageClass": "storageclasses",
        "Endpoints": "endpoints",
    }
)


def cluster_for(environment: Environment) -> Cluster:
    """The real adapter, or the recording the environment names (the demo and tests)."""
    if environment.recording:
        return RecordedCluster(environment.recording)
    return Kubectl(environment.context, kubeconfig=environment.kubeconfig)


def create_environment(
    store: Store, *, name: str, context: str, kubeconfig: str | None = None, recording: str | None = None
) -> Environment:
    name = name.strip()
    if not name:
        raise InvalidInput("the environment needs a name")
    if store.find(Environment, {"name": name}):
        raise InvalidInput(f"an environment named {name!r} exists")
    env = Environment(name=name, context=context, kubeconfig=kubeconfig, recording=recording)
    store.put(env)
    return env


def connect(store: Store, environment: Environment, cluster: Cluster | None = None) -> Environment:
    """Ask the cluster its version, its kinds and the permission matrix; keep them on the environment."""
    cluster = cluster or cluster_for(environment)
    try:
        version = cluster.version()
    except (ClusterUnreachable, PermissionRefused) as exc:
        raise ClusterUnreachable(
            f"environment {environment.name!r} (context {environment.context}) could not be reached: {exc}"
        ) from exc
    kinds = {k.split(".")[0] for k in cluster.api_resources()}
    matrix: dict[str, bool] = {}
    for verb, kind in PERMISSION_CHECKS:
        plain = kind.split(".")[0].split("/")[0]
        if plain not in kinds and kind not in ("pods/log", "pods.metrics.k8s.io", "nodes.metrics.k8s.io"):
            matrix[f"{verb} {kind}"] = False  # the kind does not exist here (a CRD that is not installed)
            continue
        try:
            matrix[f"{verb} {kind}"] = bool(cluster.can_i(verb, kind))
        except (ClusterUnreachable, PermissionRefused, NotFound):
            matrix[f"{verb} {kind}"] = False
    server_version = str(get(version, "serverVersion", "gitVersion") or "") or None
    updated = environment.model_copy(
        update={"permissions": matrix, "connected_at": now(), "server_version": server_version}
    )
    store.put(updated)
    return updated


def take_inventory(
    store: Store,
    environment: Environment,
    cluster: Cluster,
    namespaces: list[str] | None = None,
    *,
    max_items: int = 500,
) -> Inventory:
    """List every inventory kind the permissions allow and build the relationships (design §4.2, §4.3)."""
    namespaces = [ns for ns in (namespaces or []) if ns] or []
    items: list[InventoryItem] = []
    readable: list[str] = []
    unreadable: dict[str, str] = {}
    truncated: list[str] = []
    for kind in INVENTORY_KINDS:
        plural = PLURALS.get(kind, kind.lower() + "s")
        allowed = environment.permissions.get(f"list {plural}")
        if allowed is False:
            unreadable[kind] = "the cluster refuses list"
            continue
        resources: list[dict[str, Any]] = []
        try:
            for ns in namespaces or [None]:
                resources.extend(cluster.list(plural, namespace=ns))
        except PermissionRefused as exc:
            unreadable[kind] = f"the cluster refused: {exc}"
            continue
        except (ClusterUnreachable, NotFound) as exc:
            unreadable[kind] = str(exc)
            continue
        if len(resources) > max_items:
            resources = resources[:max_items]
            truncated.append(kind)
        readable.append(kind)
        for resource in resources:
            resource["kind"] = canonical_kind(str(resource.get("kind", kind)))
            items.append(item_of(resource))
    inventory = Inventory(
        environment_id=environment.id,
        namespaces=namespaces,
        items=items,
        relationships=build_relationships(items),
        readable_kinds=readable,
        unreadable=unreadable,
        truncated=truncated,
    )
    store.put(inventory)
    return inventory


def latest_inventory(store: Store, environment_id: str) -> Inventory | None:
    found = store.find(Inventory, {"environment_id": environment_id}, order_by="taken_at DESC", limit=1)
    return found[0] if found else None
