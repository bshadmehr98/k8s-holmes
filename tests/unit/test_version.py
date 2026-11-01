import re

import k8sholmes


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", k8sholmes.__version__)


def test_errors_are_exported_and_typed() -> None:
    for name in ("ClusterUnreachable", "PermissionRefused", "NotFound", "ModelFailure", "StoreVersionError"):
        assert issubclass(getattr(k8sholmes, name), k8sholmes.K8sHolmesError)
        assert name in k8sholmes.__all__
    assert k8sholmes.ModelFailure("x", retryable=True).retryable
