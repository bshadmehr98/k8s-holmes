from k8sholmes import records
from k8sholmes.environments import take_inventory
from k8sholmes.inventory import item_of
from k8sholmes.relationships import build_relationships, dependents_of
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster


def deployment(
    name: str,
    ns: str = "shop",
    replicas: int = 2,
    labels: dict[str, str] | None = None,
    env: list | None = None,
    args: list | None = None,
) -> dict:
    labels = labels or {"app": name}
    return {
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": ns,
            "generation": 3,
            "creationTimestamp": "2026-10-01T00:00:00Z",
            "annotations": {"deployment.kubernetes.io/revision": "3"},
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": f"{name}:1.2",
                            "ports": [{"containerPort": 8080}],
                            "resources": {"limits": {"memory": "256Mi"}},
                            "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8080}},
                            "env": env or [],
                            "args": args or [],
                        }
                    ],
                    "volumes": [
                        {"name": "cfg", "configMap": {"name": f"{name}-config"}},
                        {"name": "data", "persistentVolumeClaim": {"claimName": f"{name}-data"}},
                    ],
                },
            },
        },
        "status": {
            "replicas": replicas,
            "readyReplicas": replicas - 1,
            "availableReplicas": replicas - 1,
            "observedGeneration": 3,
            "conditions": [{"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"}],
        },
    }


def pod(name: str, app: str, ns: str = "shop", owner: str | None = None) -> dict:
    return {
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {"app": app},
            "ownerReferences": [{"kind": "ReplicaSet", "name": owner}] if owner else [],
        },
        "spec": {
            "nodeName": "node-1",
            "containers": [
                {
                    "name": "app",
                    "image": f"{app}:1.2",
                    "env": [{"name": "API_URL", "value": "http://api.shop.svc:8080"}],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restartCount": 4,
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                }
            ],
        },
    }


def service(
    name: str, ns: str = "shop", selector: dict[str, str] | None = None, type_: str = "ClusterIP"
) -> dict:
    return {
        "kind": "Service",
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "type": type_,
            "selector": selector if selector is not None else {"app": name},
            "ports": [{"port": 80, "targetPort": 8080}],
        },
    }


def test_item_summaries() -> None:
    d = item_of(deployment("api"))
    assert d.ref == records.ResourceRef(kind="Deployment", namespace="shop", name="api")
    assert d.summary["replicas"] == 2
    assert d.summary["ready"] == 1
    assert d.summary["containers"][0]["liveness"] is True
    assert d.summary["containers"][0]["limits"] == {"memory": "256Mi"}
    assert d.summary["revision"] == "3"
    assert d.created_at is not None
    p = item_of(pod("api-1", "api", owner="api-7d9f"))
    assert p.owner == records.ResourceRef(kind="ReplicaSet", namespace="shop", name="api-7d9f")
    assert p.summary["restarts"] == 4
    assert p.summary["container_states"][0]["last_terminated"] == "OOMKilled"
    assert p.summary["ready"] is True
    s = item_of(service("web", type_="LoadBalancer"))
    assert s.summary["type"] == "LoadBalancer"
    assert item_of(
        {"kind": "Secret", "metadata": {"name": "db", "namespace": "shop"}, "data": {"x": "y"}}
    ).summary == {"type": None, "body": "never fetched"}


def test_relationships_carry_their_basis() -> None:
    items = [
        item_of(deployment("api")),
        item_of(deployment("web", args=["--upstream=http://api.shop.svc:8080"])),
        item_of(pod("api-1", "api", owner="api-7d9f")),
        item_of(service("api")),
        item_of(service("web")),
        item_of(service("legacy-export", selector={"app": "legacy-export"})),
        item_of(
            {
                "kind": "Ingress",
                "metadata": {"name": "shop", "namespace": "shop"},
                "spec": {
                    "rules": [
                        {
                            "host": "shop.example",
                            "http": {
                                "paths": [
                                    {
                                        "path": "/",
                                        "backend": {"service": {"name": "web", "port": {"number": 80}}},
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        ),
        item_of(
            {
                "kind": "EndpointSlice",
                "metadata": {
                    "name": "api-x",
                    "namespace": "shop",
                    "labels": {"kubernetes.io/service-name": "api"},
                },
                "endpoints": [{"targetRef": {"kind": "Pod", "name": "api-1"}, "conditions": {"ready": True}}],
            }
        ),
        item_of(
            {
                "kind": "NetworkPolicy",
                "metadata": {"name": "deny-all", "namespace": "shop"},
                "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
            }
        ),
        item_of(
            {
                "kind": "HorizontalPodAutoscaler",
                "metadata": {"name": "api", "namespace": "shop"},
                "spec": {
                    "scaleTargetRef": {"kind": "Deployment", "name": "api"},
                    "minReplicas": 2,
                    "maxReplicas": 5,
                },
            }
        ),
    ]
    rels = build_relationships(items)
    as_text = {(r.source.name, r.kind, r.target.name, r.basis) for r in rels}
    assert ("api-7d9f", "owns", "api-1", "defined") in as_text
    assert ("api", "selects", "api-1", "defined") in as_text  # service -> pod by selector
    assert ("api", "selects", "api", "defined") in as_text  # service -> deployment by template labels
    assert ("api", "selects", "api-1", "observed") in as_text  # from the endpoint slice
    assert ("shop", "routes_to", "web", "defined") in as_text
    assert ("api", "references", "api-config", "defined") in as_text
    assert ("api", "mounts", "api-data", "defined") in as_text
    assert ("deny-all", "policy_selects", "api-1", "defined") in as_text
    assert ("api", "targets", "api", "defined") in as_text
    assert ("web", "mentions", "api", "inferred") in as_text
    assert not any(r.target.name == "legacy-export" and r.kind != "selects" for r in rels)
    dependents = dependents_of(records.ResourceRef(kind="Service", namespace="shop", name="api"), rels)
    assert [r.source.name for r in dependents["inferred"]] == ["web"]
    assert dependents["defined"] == []


def test_take_inventory_records_readable_unreadable_and_truncated(tmp_path) -> None:
    store = Store(tmp_path / "db")
    cluster = FakeCluster(
        [
            deployment("api"),
            pod("api-1", "api"),
            service("api"),
            {"kind": "Secret", "metadata": {"name": "db", "namespace": "shop"}, "data": {"k": "v"}},
        ]
    )
    env = records.Environment(
        name="dev", context="c", permissions={"list pods": True, "list networkpolicies": False}
    )
    inventory = take_inventory(store, env, cluster, ["shop"], max_items=2)
    kinds = {i.ref.kind for i in inventory.items}
    assert {"Deployment", "Pod", "Service", "Secret"} <= kinds
    assert inventory.unreadable["NetworkPolicy"] == "the cluster refuses list"
    assert "Pod" in inventory.readable_kinds
    assert inventory.namespaces == ["shop"]
    assert any(r.basis == "defined" for r in inventory.relationships)
    secret = next(i for i in inventory.items if i.ref.kind == "Secret")
    assert secret.summary["body"] == "never fetched"
    assert store.get(records.Inventory, inventory.id).id == inventory.id
