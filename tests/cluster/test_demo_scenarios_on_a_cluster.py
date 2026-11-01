"""The demo scenarios of design §17.2 against a real, throwaway local cluster (marker `cluster`).

The shop is applied once for the module (the OOM wait is minutes), into the namespace `shop` that the
demo investigator names.

The assertions hold for any good investigation: the capability's coverage, a finding with the expected
condition citing evidence that exists, a limitation where the source cannot answer. Never exact model text.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from k8sholmes.app import App
from k8sholmes.capabilities import Context
from k8sholmes.records import Environment, Evidence, Finding, Investigation, ResourceRef, Scope, User
from tests.cluster.conftest import has_crd, kubectl

pytestmark = [pytest.mark.cluster, pytest.mark.timeout(900)]


def run_capability(
    world: tuple[App, Environment, User], capability_id: str, inputs: dict[str, Any]
) -> Evidence:
    app, env, user = world
    ctx = Context(environment=env, cluster=app.cluster_for(env), store=app.store, user=user)
    call, evidence = app.registry.execute(capability_id, inputs, ctx, app.auth)
    assert call.status == "completed", (call.status, call.reason)
    assert evidence is not None
    return evidence


def ask(
    world: tuple[App, Environment, User], question: str, namespace: str
) -> tuple[Investigation, dict[str, Finding]]:
    app, env, user = world
    inv = app.engine.start(environment=env, question=question, user=user, scope=Scope(namespaces=[namespace]))
    inv = app.engine.run(inv.id, user)
    findings = {f.condition: f for f in app.store.find(Finding, {"investigation_id": inv.id})}
    for f in findings.values():
        for ev_id in f.evidence_for:
            assert app.store.get(Evidence, ev_id).investigation_id == inv.id  # every citation exists
    return inv, findings


def test_scenario_1_api_restarts_because_it_is_oom_killed(world, shop: str) -> None:
    health = run_capability(
        world, "k8s.workload_health", {"kind": "Deployment", "namespace": shop, "name": "api"}
    )
    assert health.coverage == "observed" and health.error is None
    states = [s for p in health.data["pods"] for s in p["summary"].get("container_states", [])]
    assert any(s.get("last_terminated") == "OOMKilled" for s in states)
    changes = run_capability(world, "k8s.changes", {"namespace": shop, "name": "api", "since_hours": 1})
    assert changes.coverage == "observed"
    assert any("lower the memory limit" in str(c.get("change_cause", "")) for c in changes.data["changes"])
    inv, findings = ask(world, "Why is api restarting?", shop)
    assert inv.status in ("completed", "partial"), inv.limitations
    assert "pod.crashloop.oom" in findings
    oom = findings["pod.crashloop.oom"]
    assert oom.resources == [ResourceRef(kind="Deployment", namespace=shop, name="api")]
    assert oom.evidence_for


def test_scenario_2_a_service_without_backends_is_never_called_unused(world, shop: str) -> None:
    backends = run_capability(world, "k8s.service_backends", {"name": "legacy-export", "namespace": shop})
    assert backends.coverage == "observed_absent" and backends.data["matching_pods"] == []
    inv, findings = ask(world, "Which services appear unused or obsolete?", shop)
    assert "service.no-backends" in findings
    assert "unused" not in findings["service.no-backends"].title.lower()
    assert any(
        "cannot_answer" in lim or "traffic" in lim.lower()
        for lim in [
            *inv.limitations,
            *findings["service.no-backends"].next_steps,
            findings["service.no-backends"].uncertainty,
        ]
    )


def test_scenario_3_isolation_names_the_exposure_and_what_it_cannot_know(world, shop: str) -> None:
    exposure = run_capability(world, "k8s.network_exposure", {"namespaces": [shop]})
    assert exposure.coverage == "observed"
    assert any(
        str(s.get("service", "")).endswith("web") and s.get("type") == "LoadBalancer"
        for s in exposure.data["exposed_services"]
    )
    inv, findings = ask(world, "Is this environment isolated from external networks?", shop)
    assert "exposure.loadbalancer" in findings
    assert any("cannot" in lim.lower() for lim in inv.limitations) or any(
        "cannot" in f.uncertainty.lower() for f in findings.values()
    )


def test_scenario_4_missing_alerts_and_safeguards_counted_by_code(world, shop: str) -> None:
    _, env, _ = world
    safeguards = run_capability(world, "k8s.safeguards", {"namespaces": [shop]})
    assert safeguards.coverage == "observed"
    rows = {r["workload"].split("/")[-1]: r for r in safeguards.data["workloads"]}
    assert rows["scheduler"]["replicas"] == 1 and rows["scheduler"]["pdb"] is False
    assert rows["worker"]["liveness_probe"] is False and rows["worker"]["limits"] is False
    assert safeguards.data["counts"]["single_replica"] == 2  # worker and scheduler
    alerts = run_capability(world, "k8s.alert_rules", {"namespaces": [shop]})
    if has_crd(env.context, "prometheusrules.monitoring.coreos.com"):
        assert alerts.coverage == "observed"
        without = {w.split("/")[-1] for w in alerts.data["workloads_without_alerts"]}
        assert {"api", "worker", "scheduler"} <= without and "web" not in without
    else:
        assert alerts.coverage == "cannot_answer"
    inv, findings = ask(world, "Which important alerts are missing?", shop)
    assert "replicas.single" in findings
    if alerts.coverage == "observed":
        assert "alert.missing" in findings
    else:
        assert any("alert" in lim.lower() for lim in inv.limitations)


def test_scenario_5_what_changed_before_the_problem(world, shop: str) -> None:
    inv, findings = ask(world, "What changed before this problem started?", shop)
    assert inv.status in ("completed", "partial")
    assert "change.before-incident" in findings
    cited = world[0].store.get(Evidence, findings["change.before-incident"].evidence_for[0])
    assert cited.source.startswith("k8s.changes")


def test_a_secret_is_read_by_name_only(world, shop: str) -> None:
    """A Secret in the namespace is listed and got by name only: its body is never fetched, so neither the
    planted value nor its base64 form (how the API returns it) reaches evidence."""
    _, env, _ = world
    planted = "tok_live_PLANTEDsecretvalue"
    kubectl(
        "create",
        "secret",
        "generic",
        "planted",
        f"--from-literal=token={planted}",
        "-n",
        shop,
        context=env.context,
    )
    encoded = base64.b64encode(planted.encode()).decode()
    inventory = run_capability(world, "k8s.inventory", {"namespaces": [shop]})
    blob = inventory.model_dump_json()
    assert "planted" in blob and planted not in blob and encoded not in blob
    got = run_capability(world, "k8s.get", {"kind": "Secret", "name": "planted", "namespace": shop})
    assert got.data["resource"]["data"] == "<never fetched>"
    blob = got.model_dump_json()
    assert planted not in blob and encoded not in blob
