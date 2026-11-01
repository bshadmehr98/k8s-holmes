"""A fake cluster and a scripted model: how the gate and the validation behave.

**What:** an investigation over resources built in memory, with a model whose replies you script. The
model asks for a probe (refused: probes are off) and cites an evidence id that does not exist (sent back
once, then turned into a limitation).
**How:** `FakeCluster`, `ScriptedModel` with a `script` function, `Engine` wired by hand, the stored
`Call` and `Finding` records.
**Why:** this is how the default test suite proves the guarantees, and how you can test your own
capability or prompt change offline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities.builtin import builtin_registry
from k8sholmes.engine import Engine
from k8sholmes.environments import connect
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster, ScriptedModel
from k8sholmes.testing.model import calls, structured, text

KINDS = (
    "pods",
    "deployments",
    "replicasets",
    "statefulsets",
    "daemonsets",
    "jobs",
    "cronjobs",
    "services",
    "endpointslices",
    "ingresses",
    "networkpolicies",
    "persistentvolumeclaims",
    "horizontalpodautoscalers",
    "poddisruptionbudgets",
    "serviceaccounts",
    "configmaps",
    "secrets",
)
cluster = FakeCluster(
    resources=[
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "api", "namespace": "shop", "labels": {"app": "api"}},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "api"}},
                "template": {
                    "metadata": {"labels": {"app": "api"}},
                    "spec": {"containers": [{"name": "app", "image": "shop/api:1.0"}]},
                },
            },
            "status": {"replicas": 1, "readyReplicas": 1},
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "api-1", "namespace": "shop", "labels": {"app": "api"}},
            "spec": {"containers": [{"name": "app", "image": "shop/api:1.0"}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"name": "app", "ready": True, "restartCount": 0}],
            },
        },
    ],
    # the kinds the cluster says it serves: the permission matrix is built from them at connection
    api_resources=[*KINDS, "events", "nodes", "namespaces"],
)


def script(messages: list[dict[str, Any]], tools: Any, schema: Any):
    """The model's turn: frame, then call two capabilities, then conclude."""
    if schema and schema.get("title") == "framing":
        return structured(
            {
                "question": "Is api healthy?",
                "namespaces": ["shop"],
                "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
                "period_hours": 24,
                "hypotheses": ["api is fine"],
                "intended_capabilities": ["k8s.workload_health"],
            }
        )
    if schema:  # the conclusion: one finding cites an evidence id that does not exist
        return structured(
            {
                "answer": "api runs one ready replica.",
                "findings": [
                    {
                        "title": "api runs a single replica",
                        "explanation": "The deployment asks for one replica and one is ready.",
                        "kind": "observation",
                        "condition": "replicas.single",
                        "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
                        "evidence_for": ["ev_made_up"],
                        "evidence_against": [],
                        "uncertainty": "",
                        "missing_information": [],
                        "next_steps": [],
                    }
                ],
                "limitations": [],
                "next_steps": [],
                "knowledge_used": [],
            }
        )
    if not any(m.get("role") == "tool" for m in messages):
        return calls(
            ("k8s.workload_health", {"kind": "Deployment", "name": "api", "namespace": "shop"}),
            ("http.probe_url", {"url": "http://api.shop.svc/"}),  # a probe: refused, probes are off
        )
    return text("done")


with tempfile.TemporaryDirectory() as tmp:
    store = Store(Path(tmp) / "k8sholmes.db")
    auth = Auth(store)
    env = records.Environment(name="lab", context="lab")
    store.put(env)
    env = connect(store, env, cluster)  # the permission matrix and the inventory
    user = auth.create_user(
        "me", "a strong one", grants=[records.Grant(environment_id=env.id, role="investigator")]
    )
    engine = Engine(
        store,
        auth,
        builtin_registry(extensions=False),
        cluster_for=lambda e: cluster,
        model_for=lambda inv: ScriptedModel(script=script),
    )

    inv = engine.start(environment=env, question="Is api healthy?", user=user)
    inv = engine.run(inv.id, user)

    by_status = {c.capability_id: c.status for c in store.find(records.Call, {"investigation_id": inv.id})}
    print("calls:", by_status)
    print("status:", inv.status)
    print("limitations:", *inv.limitations, sep="\n  ")
    assert by_status["k8s.workload_health"] == "completed"
    assert by_status["http.probe_url"] == "refused"  # the gate is code (guarantee 1)
    assert inv.status == "partial"
    assert store.find(records.Finding, {"investigation_id": inv.id}) == []  # the made-up citation kept it out
    assert any("does not exist" in lim for lim in inv.limitations)  # and the report says so (guarantee 3)
