from pathlib import Path

import pytest

from k8sholmes import records
from k8sholmes.environments import (
    PERMISSION_CHECKS,
    connect,
    create_environment,
    latest_inventory,
    take_inventory,
)
from k8sholmes.errors import ClusterUnreachable, InvalidInput
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster


def test_create_and_connect_builds_the_matrix(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    env = create_environment(store, name="dev", context="kind-dev")
    with pytest.raises(InvalidInput, match="exists"):
        create_environment(store, name="dev", context="x")
    with pytest.raises(InvalidInput, match="needs a name"):
        create_environment(store, name=" ", context="x")
    cluster = FakeCluster(
        [{"kind": "Pod", "metadata": {"name": "p", "namespace": "shop"}}],
        can_i={"list secrets *": False, "list nodes *": False},
        api_resources=["pods", "deployments.apps", "events", "nodes", "secrets"],
    )
    env = connect(store, env, cluster)
    assert env.permissions["list pods"] is True
    assert env.permissions["list secrets"] is False
    assert env.permissions["list nodes"] is False
    assert "list prometheusrules" not in env.permissions  # the CRD is not installed: nothing to refuse
    assert env.permissions["get pods/log"] is True
    assert env.server_version == "v1.36.0"
    assert env.connected_at is not None
    served = {"pods", "deployments", "events", "nodes", "secrets"}
    always = {"pods/log", "pods.metrics.k8s.io", "nodes.metrics.k8s.io"}
    expected = {f"{v} {k}" for v, k in PERMISSION_CHECKS if k in served | always}
    assert set(env.permissions) == expected
    assert "list servicemonitors" not in env.permissions
    assert store.get(records.Environment, env.id).permissions == env.permissions


def test_connect_failure_is_typed(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    env = create_environment(store, name="dev", context="kind-dev")

    class Down(FakeCluster):
        def version(self):  # type: ignore[override]
            raise ClusterUnreachable("refused")

    with pytest.raises(ClusterUnreachable, match="could not be reached: refused"):
        connect(store, env, Down())


def test_latest_inventory(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    env = records.Environment(name="dev", context="c")
    assert latest_inventory(store, env.id) is None
    first = take_inventory(store, env, FakeCluster(), [])
    second = take_inventory(store, env, FakeCluster(), [])
    latest = latest_inventory(store, env.id)
    assert latest is not None
    assert latest.id in (first.id, second.id)
    assert latest.taken_at >= first.taken_at
