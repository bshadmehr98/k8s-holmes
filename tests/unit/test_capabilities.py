"""The built-in capabilities on the demo cluster: what they read and the coverage they state."""

import json
from pathlib import Path

import pytest

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context
from k8sholmes.capabilities.builtin import builtin_registry
from k8sholmes.environments import connect
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster
from k8sholmes.testing.demo import PLANTED_BEARER, PLANTED_TOKEN, build
from tests.helpers import closed_port


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    store = Store(tmp_path_factory.mktemp("db") / "k8sholmes.db")
    auth = Auth(store)
    cluster = build()
    env = records.Environment(name="demo", context="demo")
    store.put(env)
    env = connect(store, env, cluster)
    user = auth.create_user(
        "inv", "a strong one", grants=[records.Grant(environment_id=env.id, role="operator")]
    )
    registry = builtin_registry(extensions=False)
    return store, auth, env, user, cluster, registry


def run(world, capability: str, cluster: FakeCluster | None = None, **inputs):
    store, auth, env, user, demo, registry = world
    ctx = Context(
        environment=env,
        cluster=cluster or demo,
        store=store,
        user=user,
        investigation_id="inv_t",
    )
    call, evidence = registry.execute(capability, inputs, ctx, auth, step_id="stp_t")
    assert call.status == "completed", call.reason
    assert evidence is not None
    return evidence


def test_catalog_has_the_designed_capabilities(world) -> None:
    registry = world[-1]
    ids = {c.id for c in registry.all()}
    assert ids == {
        "k8s.inventory",
        "k8s.get",
        "k8s.list",
        "k8s.events",
        "k8s.logs",
        "k8s.workload_health",
        "k8s.changes",
        "k8s.network_exposure",
        "k8s.service_backends",
        "k8s.safeguards",
        "k8s.alert_rules",
        "k8s.permissions",
        "k8s.top",
        "knowledge.search",
        "evidence.recall",
        "http.probe_url",
    }
    for cap in registry.all():
        assert cap.purpose and cap.coverage
        assert cap.describe()["inputs"]["type"] == "object"
    assert registry.get("http.probe_url").class_ == "probe"


def test_inventory_counts_and_relationships(world) -> None:
    ev = run(world, "k8s.inventory", namespaces=["shop"])
    assert ev.coverage == "observed"
    counts = ev.data["counts"]
    assert counts["Deployment"] == 5
    assert counts["Service"] == 4
    assert counts["Secret"] == 1
    bases = {r["basis"] for r in ev.data["relationships"]}
    assert bases == {"defined", "observed", "inferred"}
    assert all(
        i["summary"].get("body") == "never fetched" for i in ev.data["items"] if i["ref"]["kind"] == "Secret"
    )


def test_workload_health_reads_the_crash_evidence(world) -> None:
    ev = run(world, "k8s.workload_health", kind="deployment", name="api", namespace="shop")
    assert ev.coverage == "observed"
    assert "1/2 pods ready" in ev.summary
    states = [s for p in ev.data["pods"] for s in p["summary"]["container_states"]]
    assert any(s["last_terminated"] == "OOMKilled" and s["waiting"] == "CrashLoopBackOff" for s in states)
    assert any("out of memory" in text for text in ev.data["previous_logs"].values())
    assert any(
        e["reason"] == "BackOff"
        for pod_events in ev.data["unhealthy_pod_events"].values()
        for e in pod_events
    )
    assert [o["summary"]["revision"] for o in ev.data["owner_chain"]] == ["2", "3"]


def test_changes_find_the_revision_before_the_problem(world) -> None:
    ev = run(world, "k8s.changes", namespace="shop", since_hours=48, name="api")
    revisions = [c for c in ev.data["changes"] if c["kind"] == "revision"]
    assert any(c["change_cause"] and "256Mi" in c["change_cause"] for c in revisions)
    assert ev.coverage == "observed"
    old = run(world, "k8s.changes", namespace="shop", since_hours=48, name="worker")
    assert old.data["changes"] == []
    assert old.coverage == "no_data"


def test_events_state_no_data_beyond_retention(world) -> None:
    recent = run(world, "k8s.events", namespace="shop", kind="Pod", name="api-7d9f-x1y2z", since_hours=1)
    assert recent.coverage == "observed"
    assert {e["reason"] for e in recent.data["events"]} == {"BackOff", "Unhealthy"}
    none = run(world, "k8s.events", namespace="shop", kind="Pod", name="scheduler-1b2c-5h7jr", since_hours=24)
    assert none.coverage == "no_data"
    assert "about an hour" in none.coverage_reason
    none_short = run(
        world, "k8s.events", namespace="shop", kind="Pod", name="scheduler-1b2c-5h7jr", since_hours=1
    )
    assert none_short.coverage == "observed_absent"


def test_logs_redact_planted_secrets(world) -> None:
    ev = run(world, "k8s.logs", pod="web-5f0c-k2m8p", namespace="shop", container="web")
    assert PLANTED_BEARER not in ev.data
    assert ev.redactions >= 1
    assert "502" in ev.data
    previous = run(world, "k8s.logs", pod="api-7d9f-x1y2z", namespace="shop", container="app", previous=True)
    assert "out of memory" in previous.data
    empty = run(world, "k8s.logs", pod="scheduler-1b2c-5h7jr", namespace="shop", container="other")
    assert empty.coverage == "no_data"


def test_get_and_list_coverage(world) -> None:
    got = run(world, "k8s.get", kind="deployment", name="api", namespace="shop")
    assert got.coverage == "observed"
    assert got.data["summary"]["replicas"] == 2
    missing = run(world, "k8s.get", kind="deployment", name="ghost", namespace="shop")
    assert missing.coverage == "observed_absent"
    secret = run(world, "k8s.get", kind="secret", name="db-creds", namespace="shop")
    assert secret.data["resource"]["data"] == "<never fetched>"
    listed = run(world, "k8s.list", kind="pods", namespace="shop", selector="app=api")
    assert len(listed.data["items"]) == 2
    nothing = run(world, "k8s.list", kind="pods", namespace="shop", selector="app=nothing")
    assert nothing.coverage == "observed_absent"
    worker = run(world, "k8s.get", kind="deployment", name="worker", namespace="shop")
    assert PLANTED_TOKEN not in str(worker.data)
    assert worker.redactions >= 1


def test_service_backends_and_the_unused_question(world) -> None:
    legacy = run(world, "k8s.service_backends", name="legacy-export", namespace="shop")
    assert legacy.coverage == "observed_absent"
    assert legacy.data["matching_pods"] == []
    assert legacy.data["traffic"]["coverage"] == "cannot_answer"
    api = run(world, "k8s.service_backends", name="api", namespace="shop")
    assert api.coverage == "observed"
    assert api.data["ready_endpoints"] == 1
    assert all(p["matches_a_container_port"] for p in api.data["ports"])


def test_network_exposure_says_what_it_cannot_know(world) -> None:
    ev = run(world, "k8s.network_exposure", namespaces=["shop"])
    assert [s["service"] for s in ev.data["exposed_services"]] == ["Service/shop/web"]
    assert ev.data["ingresses"][0]["hosts"] == ["shop.example"]
    assert "Pod/shop/postgres-0" not in ev.data["pods_selected_by_no_policy"]
    assert "Pod/shop/web-5f0c-k2m8p" in ev.data["pods_selected_by_no_policy"]
    assert ev.data["outside_cluster"]["coverage"] == "cannot_answer"
    assert "cannot be answered" in ev.coverage_reason


def test_safeguards_counts_are_computed(world) -> None:
    ev = run(world, "k8s.safeguards", namespaces=["shop"])
    rows = {r["workload"]: r for r in ev.data["workloads"]}
    assert rows["Deployment/shop/worker"]["liveness_probe"] is False
    assert rows["Deployment/shop/worker"]["limits"] is False
    assert rows["Deployment/shop/worker"]["latest_tag"] is True
    assert rows["Deployment/shop/web"]["pdb"] is True
    assert rows["Deployment/shop/web"]["hpa"] is True
    assert rows["Deployment/shop/scheduler"]["replicas"] == 1
    counts = ev.data["counts"]
    assert counts["workloads"] == len(ev.data["workloads"]) == 6
    assert counts["single_replica"] == sum(
        1 for r in ev.data["workloads"] if r["kind"] != "DaemonSet" and (r["replicas"] or 0) <= 1
    )


def test_alert_rules_and_their_absence(world) -> None:
    ev = run(world, "k8s.alert_rules", namespaces=["shop"])
    assert ev.coverage == "observed"
    assert {r["alert"] for r in ev.data["rules"]} == {"WebHighErrorRate", "WebDown", "PostgresDown"}
    without = set(ev.data["workloads_without_alerts"])
    assert {"Deployment/shop/api", "Deployment/shop/worker", "Deployment/shop/scheduler"} <= without
    assert "Deployment/shop/web" not in without
    assert ev.data["fired"]["coverage"] == "cannot_answer"
    no_crd = FakeCluster(api_resources=["pods", "deployments"])
    none = run(world, "k8s.alert_rules", cluster=no_crd, namespaces=["shop"])
    assert none.coverage == "cannot_answer"


def test_alert_rules_on_a_cluster_without_the_crd_is_cannot_answer_not_refused(tmp_path: Path) -> None:
    """Design §5.1: an absent CRD is a source that cannot answer, not a permission the gate refuses."""
    store = Store(tmp_path / "k8sholmes.db")
    auth = Auth(store)
    cluster = FakeCluster(api_resources=["pods", "deployments.apps"])
    env = records.Environment(name="bare", context="bare")
    store.put(env)
    env = connect(store, env, cluster)
    user = auth.create_user(
        "inv", "a strong one", grants=[records.Grant(environment_id=env.id, role="operator")]
    )
    ctx = Context(environment=env, cluster=cluster, store=store, user=user, investigation_id="inv_t")
    call, evidence = builtin_registry(extensions=False).execute(
        "k8s.alert_rules", {"namespaces": ["shop"]}, ctx, auth
    )
    assert call.status == "completed", call.reason
    assert evidence is not None and evidence.coverage == "cannot_answer"
    assert "PrometheusRule CRD is not installed" in evidence.coverage_reason
    _, perms = builtin_registry(extensions=False).execute("k8s.permissions", {}, ctx, auth)
    assert perms is not None  # the absence is still evidence: checked, and not served here
    assert {"list prometheusrules", "list servicemonitors"} <= set(perms.data["not_served"])
    assert "not served: " in perms.summary and "list prometheusrules" not in perms.data["denied"]


def test_permissions_top_knowledge_and_recall(world) -> None:
    perms = run(world, "k8s.permissions")
    assert perms.data["permissions"]["list pods"] is True
    top = run(world, "k8s.top", kind="pods", namespace="shop")
    assert top.coverage == "observed"
    no_metrics = run(world, "k8s.top", cluster=FakeCluster(), kind="nodes")
    assert no_metrics.coverage == "cannot_answer"
    store, _, env, *_ = world
    store.put(
        records.Knowledge(
            environment_id=env.id,
            kind="ownership",
            scope=records.ResourceRef(kind="Deployment", namespace="shop", name="api"),
            text="team checkout",
            author="user:u",
        )
    )
    known = run(world, "knowledge.search", namespace="shop", kind="Deployment", name="api")
    assert known.data["knowledge"][0]["text"] == "team checkout"
    assert "not evidence" in known.summary
    nothing = run(world, "knowledge.search", namespace="other", kind="Deployment", name="x")
    assert nothing.coverage == "observed_absent"
    recall = run(world, "evidence.recall", namespace="shop")
    assert recall.coverage == "observed_absent"


def test_probe_is_refused_until_enabled_and_refuses_other_schemes(world) -> None:
    store, auth, env, user, demo, registry = world
    port = closed_port()
    ctx = Context(environment=env, cluster=demo, store=store, user=user)
    call, evidence = registry.execute("http.probe_url", {"url": f"http://127.0.0.1:{port}/"}, ctx, auth)
    assert call.status == "refused"
    assert "probes are not enabled" in call.reason
    enabled = env.model_copy(update={"settings": env.settings.model_copy(update={"probes_enabled": True})})
    ctx = Context(environment=enabled, cluster=demo, store=store, user=user)
    call, evidence = registry.execute(
        "http.probe_url", {"url": f"http://127.0.0.1:{port}/", "timeout_ms": 300}, ctx, auth
    )
    assert call.status == "completed"
    assert evidence is not None
    assert evidence.error is not None  # nothing listens there
    assert evidence.coverage == "observed"
    _, ev = registry.execute("http.probe_url", {"url": "file:///etc/passwd"}, ctx, auth)
    assert ev is not None
    assert ev.coverage == "cannot_answer"
    assert ev.data is None
    assert "only http and https" in ev.coverage_reason
    assert "root:" not in json.dumps(ev.model_dump(mode="json"))


def test_workload_health_and_recall_coverage(world) -> None:
    missing = run(world, "k8s.workload_health", kind="deployment", name="ghost", namespace="shop")
    assert missing.coverage == "observed_absent"
    store, _, env, *_ = world
    ev = records.Evidence(
        investigation_id="inv_other",
        environment_id=env.id,
        source="k8s.safeguards",
        coverage="observed",
        summary="earlier",
    )
    store.put(ev)
    store.put(
        records.Finding(
            investigation_id="inv_other",
            environment_id=env.id,
            title="t",
            explanation="e",
            condition="replicas.single",
            resources=[records.ResourceRef(kind="Deployment", namespace="shop", name="scheduler")],
            evidence_for=[ev.id],
            author="model:m",
        )
    )
    recalled = run(world, "evidence.recall", namespace="shop", name="scheduler")
    assert recalled.coverage == "observed"
    assert recalled.data["findings"][0]["condition"] == "replicas.single"
    assert [e["id"] for e in recalled.data["evidence"]] == [ev.id]


def test_demo_after_fix_is_healthy(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    auth = Auth(store)
    cluster = build(after_fix=True)
    env = connect(store, records.Environment(name="demo", context="demo"), cluster)
    store.put(env)
    user = auth.create_user("u", "a strong one", is_admin=True)
    registry = builtin_registry(extensions=False)
    ctx = Context(
        environment=env,
        cluster=cluster,
        store=store,
        user=user,
    )
    _, ev = registry.execute(
        "k8s.workload_health", {"kind": "deployment", "name": "api", "namespace": "shop"}, ctx, auth
    )
    assert ev is not None
    assert "2/2 pods ready, 0 unhealthy, 0 restarts" in ev.summary
    assert ev.data["previous_logs"] == {}
