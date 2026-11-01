"""Every Cluster adapter and fake keeps the contract (design §17.1)."""

import os
import stat
from pathlib import Path

import pytest

from k8sholmes.cluster import Kubectl
from k8sholmes.testing import FakeCluster, RecordedCluster, check_cluster
from k8sholmes.testing.demo import build
from tests.integration.test_kubectl import FAKE


def test_fake_cluster_and_the_demo() -> None:
    check_cluster(build(), namespace="shop", secret_name="db-creds")
    check_cluster(build(after_fix=True), namespace="shop", secret_name="db-creds")
    check_cluster(FakeCluster(), namespace="none")


def test_the_saved_demo_recording(tmp_path: Path) -> None:
    build().save(tmp_path / "rec")
    check_cluster(RecordedCluster(tmp_path / "rec"), namespace="shop", secret_name="db-creds")


def test_kubectl_against_the_fake_kubectl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "kubectl"
    script.write_text(FAKE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_KUBECTL_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_KUBECTL_MODE", "notfound_for_missing")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    check_cluster(Kubectl("kind-dev"), namespace="shop", secret_name="db")
