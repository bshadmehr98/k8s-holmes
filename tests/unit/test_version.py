import re

import k8sholmes


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", k8sholmes.__version__)


def test_errors_are_exported_and_typed() -> None:
    assert issubclass(k8sholmes.ClusterUnreachable, k8sholmes.K8sHolmesError)
    assert issubclass(k8sholmes.InvalidCase, k8sholmes.K8sHolmesError)
    assert issubclass(k8sholmes.ToolMissing, k8sholmes.K8sHolmesError)
    assert set(k8sholmes.__all__) >= {"K8sHolmesError", "ClusterUnreachable", "InvalidCase", "ToolMissing"}
