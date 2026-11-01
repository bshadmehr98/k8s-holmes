"""The kubectl adapter builds the right argument lists and maps failures, against a fake kubectl on the PATH."""

import json
import os
import stat
from pathlib import Path

import pytest

from k8sholmes.cluster import Kubectl
from k8sholmes.errors import ClusterUnreachable, InvalidInput, NotFound, PermissionRefused, ToolMissing
from k8sholmes.records import ResourceRef
from k8sholmes.testing import RecordedCluster

FAKE = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = open(os.environ["FAKE_KUBECTL_LOG"], "a")
log.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_KUBECTL_MODE", "ok")
if mode == "forbidden":
    sys.stderr.write('Error from server (Forbidden): pods is forbidden: User "x" cannot list resource "pods"\n'); sys.exit(1)
if mode == "notfound":
    sys.stderr.write('Error from server (NotFound): pods "ghost" not found\n'); sys.exit(1)
if mode == "notfound_for_missing" and "this-pod-does-not-exist-9f3a" in args:
    sys.stderr.write('Error from server (NotFound): pods "this-pod-does-not-exist-9f3a" not found\n'); sys.exit(1)
if mode == "down":
    sys.stderr.write('The connection to the server 127.0.0.1:6443 was refused\n'); sys.exit(1)
if mode == "nometrics":
    sys.stderr.write('error: Metrics API not available\n'); sys.exit(1)
skip = False
positional = []
for a in args:
    if skip:
        skip = False; continue
    if a in ("--context", "--request-timeout", "--kubeconfig", "--namespace", "--selector", "--field-selector", "-o", "--tail", "--container", "--since-time"):
        skip = True; continue
    if not a.startswith("--"):
        positional.append(a)
verb = positional[0] if positional else ""
if "auth" in args:
    print("yes" if "pods" in args else "no"); sys.exit(0 if "pods" in args else 1)
if verb == "version":
    print(json.dumps({"clientVersion": {"gitVersion": "v1.36.2"}, "serverVersion": {"gitVersion": "v1.33.0"}})); sys.exit(0)
if verb == "api-resources":
    print("pods\ndeployments.apps\nprometheusrules.monitoring.coreos.com"); sys.exit(0)
if verb == "logs":
    print("2026-11-01T10:00:00Z hello"); sys.exit(0)
if verb == "top":
    print("shop api-1 10m 50Mi" if "--all-namespaces" in args else "api-1 10m 50Mi"); sys.exit(0)
if "secret" in args and "-o" in args and args[args.index("-o") + 1] == "name":
    print("secret/db"); sys.exit(0)
if "secrets" in args:
    print("shop/db\nshop/tls"); sys.exit(0)
if "events" in args:
    print(json.dumps({"items": [{"reason": "BackOff", "lastTimestamp": "2026-11-01T10:00:00Z"}]})); sys.exit(0)
if "api" in args and "deployment" in args:
    print(json.dumps({"kind": "Deployment", "metadata": {"name": "api", "namespace": "shop"}})); sys.exit(0)
print(json.dumps({"kind": "List", "items": [{"kind": "Pod", "metadata": {"name": "api-1", "namespace": "shop"}}]}))
"""


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "kubectl"
    script.write_text(FAKE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_KUBECTL_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_arguments_are_explicit_and_read_only(fake: Path, tmp_path: Path) -> None:
    k = Kubectl("kind-dev", kubeconfig=str(tmp_path / "kc"), record=tmp_path / "rec")
    assert k.get("deployment", "api", namespace="shop")["metadata"]["name"] == "api"
    pods = k.list("pods", namespace="shop", selector="app=api", field_selector="status.phase=Running")
    assert pods[0]["metadata"]["name"] == "api-1"
    k.events(involved=ResourceRef(kind="Pod", namespace="shop", name="api-1"), namespace="shop")
    assert k.logs("api-1", namespace="shop", container="app", previous=True, tail=50).startswith("2026-")
    assert k.top("pods") == [{"namespace": "shop", "name": "api-1", "cpu": "10m", "memory": "50Mi"}]
    assert k.can_i("list", "pods", namespace="shop")
    assert not k.can_i("list", "nodes")
    assert k.version()["serverVersion"]["gitVersion"] == "v1.33.0"
    assert "prometheusrules.monitoring.coreos.com" in k.api_resources()
    made = calls(fake)
    first = made[0]
    assert first[:4] == ["--context", "kind-dev", "--request-timeout", "20s"]
    assert "--kubeconfig" in first
    assert made[1][6:] == [
        "get",
        "pods",
        "--namespace",
        "shop",
        "--selector",
        "app=api",
        "--field-selector",
        "status.phase=Running",
        "-o",
        "json",
    ]
    assert "--field-selector" in made[2] and "involvedObject.name=api-1,involvedObject.kind=Pod" in made[2]
    assert made[3][6:] == [
        "logs",
        "api-1",
        "--namespace",
        "shop",
        "--tail",
        "50",
        "--timestamps",
        "--container",
        "app",
        "--previous",
    ]
    mutating = {"delete", "apply", "create", "patch", "edit", "exec", "scale", "replace", "rollout"}
    for c in made:
        assert not mutating & set(c), c
    replay = RecordedCluster(tmp_path / "rec")
    assert replay.get("Deployment", "api", namespace="shop")["metadata"]["name"] == "api"
    assert replay.list("pods")[0]["metadata"]["name"] == "api-1"
    assert replay.logs("api-1", namespace="shop", container="app", previous=True).startswith("2026-")
    assert replay.can_i("list", "pods", namespace="shop")


def test_secrets_are_listed_and_checked_by_name_only(fake: Path) -> None:
    k = Kubectl("kind-dev")
    assert k.get("secret", "db", namespace="shop") == {
        "kind": "Secret",
        "metadata": {"name": "db", "namespace": "shop"},
        "data": "<never fetched>",
    }
    names = [s["metadata"]["name"] for s in k.list("secrets", namespace="shop")]
    assert names == ["db", "tls"]
    for c in calls(fake):
        assert "-o" in c
        output = c[c.index("-o") + 1]
        assert output == "name" or output.startswith("jsonpath=")


def test_failures_are_typed(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k = Kubectl("kind-dev")
    monkeypatch.setenv("FAKE_KUBECTL_MODE", "forbidden")
    with pytest.raises(PermissionRefused, match="Forbidden"):
        k.list("pods")
    monkeypatch.setenv("FAKE_KUBECTL_MODE", "notfound")
    with pytest.raises(NotFound):
        k.get("pod", "ghost", namespace="shop")
    monkeypatch.setenv("FAKE_KUBECTL_MODE", "down")
    with pytest.raises(ClusterUnreachable, match="refused"):
        k.version()
    monkeypatch.setenv("FAKE_KUBECTL_MODE", "nometrics")
    assert k.top("nodes") is None


def test_only_read_only_verbs_and_valid_names(fake: Path) -> None:
    k = Kubectl("kind-dev")
    with pytest.raises(PermissionRefused, match="not a read-only verb"):
        k._run(["delete", "pod", "x"])
    with pytest.raises(InvalidInput):
        k.get("pod", "--all", namespace="shop")
    with pytest.raises(InvalidInput):
        k.logs("a pod", namespace="shop")
    with pytest.raises(InvalidInput):
        k.top("deployments")


def test_missing_kubectl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ToolMissing, match="install kubectl"):
        Kubectl("kind-dev").version()
