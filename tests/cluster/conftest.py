"""Fixtures for the cluster tests (CONTRIBUTING.md, "Cluster tests"): a throwaway local cluster chosen by
`KUBECONFIG` and `K8SHOLMES_TEST_CONTEXT`, and the demo shop of design §17.2 applied once per module into
a namespace named `shop` (the demo investigator's namespace), deleted afterwards."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from k8sholmes.app import DEMO_ENDPOINT_ID, App
from k8sholmes.cluster import Kubectl
from k8sholmes.environments import connect, create_environment
from k8sholmes.records import Environment, ModelEndpoint, User

MANIFESTS = Path(__file__).parent / "manifests"
LOCAL_CONTEXT_PREFIXES = ("kind-", "minikube")
SHOP = "shop"


def _kubectl(
    *args: str, context: str, check: bool = True, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    command = ["kubectl", "--context", context, "--request-timeout", "30s", *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=check)


@pytest.fixture(scope="session")
def context() -> str:
    """The kubeconfig context of the throwaway cluster, or a skip with the reason."""
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl is not on the PATH")
    name = os.environ.get("K8SHOLMES_TEST_CONTEXT")
    if not name:
        current = subprocess.run(
            ["kubectl", "config", "current-context"], capture_output=True, text=True, check=False
        )
        name = current.stdout.strip()
    if not name:
        pytest.skip(
            "no kubeconfig context: set K8SHOLMES_TEST_CONTEXT (and KUBECONFIG) to a kind or minikube cluster"
        )
    local = name.startswith(LOCAL_CONTEXT_PREFIXES)
    if not local and os.environ.get("K8SHOLMES_TEST_ALLOW_CONTEXT") != "1":
        pytest.skip(
            f"context {name!r} does not look like a throwaway cluster (kind-* or minikube); set K8SHOLMES_TEST_ALLOW_CONTEXT=1 to use it anyway"
        )
    probe = _kubectl("version", "-o", "json", context=name, check=False, timeout=30)
    if probe.returncode != 0:
        pytest.skip(f"the cluster behind context {name!r} does not answer: {probe.stderr.strip()[:200]}")
    return name


def has_crd(context: str, name: str) -> bool:
    return _kubectl("get", "crd", name, context=context, check=False, timeout=30).returncode == 0


def wait_for(predicate: Callable[[], bool], *, timeout_s: float, every_s: float = 2.0, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(every_s)
    pytest.fail(f"{what} did not happen within {timeout_s:g} s")


def pods(context: str, namespace: str, selector: str) -> list[dict]:
    out = _kubectl("get", "pods", "-n", namespace, "-l", selector, "-o", "json", context=context)
    return json.loads(out.stdout)["items"]


def oom_killed(context: str, namespace: str) -> bool:
    for pod in pods(context, namespace, "app=api"):
        for status in pod.get("status", {}).get("containerStatuses", []):
            if status.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled":
                return True
    return False


@pytest.fixture(scope="module")
def shop(context: str) -> Iterator[str]:
    """The demo shop in the namespace `shop`: the first api revision, then the lower limit, then the alert
    rules when the CRD exists; waits until api has been OOM-killed. Skips when `shop` already exists, so
    nothing of the cluster's own is touched; deletes the namespace afterwards, even when a test fails."""
    if _kubectl("get", "namespace", SHOP, context=context, check=False, timeout=30).returncode == 0:
        pytest.skip(f"the namespace {SHOP!r} exists on context {context!r}; the suite needs to create it")
    _kubectl("create", "namespace", SHOP, context=context)
    try:

        def apply(relative: str) -> None:
            _kubectl("apply", "-n", SHOP, "-f", str(MANIFESTS / relative), context=context)

        apply("shop/00-workloads.yaml")
        wait_for(lambda: bool(pods(context, SHOP, "app=api")), timeout_s=120, what="api pods appearing")
        apply("shop/10-api-lower-limit.yaml")
        if has_crd(context, "prometheusrules.monitoring.coreos.com"):
            apply("shop/20-alert-rules.yaml")
        wait_for(lambda: oom_killed(context, SHOP), timeout_s=300, every_s=5, what="api being OOM-killed")
        yield SHOP
    finally:
        _kubectl("delete", "namespace", SHOP, "--wait=false", context=context, check=False)


@pytest.fixture
def cluster(context: str) -> Kubectl:
    """The real adapter; with `K8SHOLMES_RECORD_TO` set, every request and reply is recorded there. The
    recording is raw (the redactor runs on evidence, not here): hand-check it before committing it."""
    record = os.environ.get("K8SHOLMES_RECORD_TO")
    return Kubectl(context, record=Path(record) if record else None)


@pytest.fixture
def world(context: str, cluster: Kubectl, tmp_path: Path) -> tuple[App, Environment, User]:
    """An `App` on a temporary home whose environment is the real cluster and whose model is the offline
    demo investigator, so the scenarios' questions run end to end without a hosted model."""
    app = App.open(tmp_path / "home", extensions=False)
    env = create_environment(app.store, name="cluster", context=context)
    env = env.model_copy(
        update={"settings": env.settings.model_copy(update={"default_model_id": DEMO_ENDPOINT_ID})}
    )
    app.store.put(env)
    app.clusters[env.id] = cluster
    env = connect(app.store, env, cluster)
    app.store.put(
        ModelEndpoint(
            id=DEMO_ENDPOINT_ID, name="demo investigator", base_url="demo://", model="demo-investigator"
        )
    )
    user = app.auth.create_user("operator", "a strong one")
    user = app.auth.set_grant(user, env.id, "operator")
    return app, env, user
