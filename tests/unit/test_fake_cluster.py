from pathlib import Path

import pytest

from k8sholmes.errors import NotFound
from k8sholmes.records import ResourceRef
from k8sholmes.testing import FakeCluster, RecordedCluster, check_cluster, matches_fields, matches_selector


def pod(name: str, ns: str = "shop", labels: dict[str, str] | None = None, phase: str = "Running") -> dict:
    return {
        "kind": "Pod",
        "metadata": {"name": name, "namespace": ns, "labels": labels or {}},
        "status": {"phase": phase},
    }


@pytest.fixture
def cluster() -> FakeCluster:
    c = FakeCluster(
        [
            pod("api-1", labels={"app": "api", "tier": "backend"}),
            pod("web-1", labels={"app": "web"}, phase="Pending"),
            {"kind": "deployment", "metadata": {"name": "api", "namespace": "shop"}},
            {"kind": "Secret", "metadata": {"name": "db", "namespace": "shop"}, "data": {"password": "aGk="}},
        ],
        events=[
            {
                "metadata": {"namespace": "shop"},
                "lastTimestamp": "2026-11-01T10:00:00Z",
                "involvedObject": {"kind": "Pod", "name": "api-1"},
                "reason": "BackOff",
            },
            {
                "metadata": {"namespace": "shop"},
                "lastTimestamp": "2026-11-01T09:00:00Z",
                "involvedObject": {"kind": "Pod", "name": "web-1"},
                "reason": "FailedScheduling",
            },
        ],
        logs={
            "shop__api-1__app": "2026-11-01T09:59:00Z line1\n2026-11-01T10:00:01Z line2\n",
            "shop__api-1__app__previous": "old\n",
        },
        top={"pods": [{"namespace": "shop", "name": "api-1", "cpu": "10m", "memory": "50Mi"}], "nodes": None},
        can_i={"list pods shop": True, "list secrets *": False},
    )
    return c


def test_selectors() -> None:
    labels = {"app": "api", "tier": "backend"}
    assert matches_selector(labels, "app=api")
    assert matches_selector(labels, "app==api,tier")
    assert not matches_selector(labels, "app!=api")
    assert matches_selector(labels, "app in (api, web)")
    assert not matches_selector(labels, "app notin (api)")
    assert matches_selector(labels, "!other")
    assert not matches_selector(labels, "other")
    assert matches_selector(labels, None)
    assert matches_fields(
        {"metadata": {"name": "a"}, "status": {"phase": "Running"}}, "metadata.name=a,status.phase!=Pending"
    )
    assert not matches_fields({"metadata": {"name": "a"}}, "metadata.name=b")


def test_get_list_and_kind_aliases(cluster: FakeCluster) -> None:
    assert cluster.get("deploy", "api", namespace="shop")["kind"] == "Deployment"
    assert [p["metadata"]["name"] for p in cluster.list("po", namespace="shop", selector="app=api")] == [
        "api-1"
    ]
    assert [p["metadata"]["name"] for p in cluster.list("pods", field_selector="status.phase=Pending")] == [
        "web-1"
    ]
    with pytest.raises(NotFound, match='Pod "nope" not found in namespace shop'):
        cluster.get("pod", "nope", namespace="shop")
    assert cluster.calls[0] == ("get", {"kind": "deploy", "name": "api", "namespace": "shop"})


def test_secret_bodies_are_never_returned(cluster: FakeCluster) -> None:
    assert cluster.get("secret", "db", namespace="shop")["data"] == "<never fetched>"
    assert cluster.list("secrets", namespace="shop")[0]["data"] == "<never fetched>"


def test_events_are_sorted_and_filtered(cluster: FakeCluster) -> None:
    assert [e["reason"] for e in cluster.events(namespace="shop")] == ["FailedScheduling", "BackOff"]
    only = cluster.events(involved=ResourceRef(kind="pod", namespace="shop", name="api-1"))
    assert [e["reason"] for e in only] == ["BackOff"]
    assert cluster.list("events", namespace="shop") == cluster.events(namespace="shop")


def test_logs_tail_previous_and_missing_pod(cluster: FakeCluster) -> None:
    assert cluster.logs("api-1", namespace="shop", tail=1) == "2026-11-01T10:00:01Z line2\n"
    assert cluster.logs("api-1", namespace="shop", container="app", previous=True) == "old\n"
    assert cluster.logs("web-1", namespace="shop") == ""
    with pytest.raises(NotFound):
        cluster.logs("ghost", namespace="shop")


def test_top_can_i_version_api_resources(cluster: FakeCluster) -> None:
    assert cluster.top("pods", namespace="shop") == [
        {"namespace": "shop", "name": "api-1", "cpu": "10m", "memory": "50Mi"}
    ]
    assert cluster.top("pods", namespace="other") == []
    assert cluster.top("nodes") is None
    assert cluster.can_i("list", "pods", namespace="shop")
    assert not cluster.can_i("list", "secrets", namespace="shop")
    assert cluster.can_i("get", "nodes")  # the default
    assert "serverVersion" in cluster.version()
    assert "pods" in cluster.api_resources()


def test_save_and_replay_recording(cluster: FakeCluster, tmp_path: Path) -> None:
    cluster.save(tmp_path / "rec")
    replayed = RecordedCluster(tmp_path / "rec")
    assert replayed.context == "fake"
    assert [p["metadata"]["name"] for p in replayed.list("pods", namespace="shop")] == ["api-1", "web-1"]
    assert replayed.logs("api-1", namespace="shop", container="app", previous=True) == "old\n"
    assert replayed.get("secret", "db", namespace="shop")["data"] == "<never fetched>"
    assert not (tmp_path / "rec" / "resources" / "Secret.json").read_text().count("aGk=")
    assert replayed.top("nodes") is None
    assert not replayed.can_i("list", "secrets")
    with pytest.raises(NotFound):
        RecordedCluster(tmp_path / "missing")


def test_contract(cluster: FakeCluster) -> None:
    check_cluster(cluster, namespace="shop", secret_name="db")
