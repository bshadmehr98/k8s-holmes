"""The composition root: the secrets file, choosing a model, choosing a cluster adapter."""

from pathlib import Path

import pytest

from k8sholmes import records
from k8sholmes.app import App
from k8sholmes.errors import InvalidInput
from k8sholmes.models import api_key_for, read_secrets, write_secret
from k8sholmes.testing.demo_model import DemoModel


def test_secrets_file_is_private_and_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_secret(tmp_path, "mdl_1", "sk-file-key")
    path = tmp_path / "secrets.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert read_secrets(tmp_path) == {"mdl_1": "sk-file-key"}
    endpoint = records.ModelEndpoint(id="mdl_1", name="e", base_url="http://x/v1", model="m")
    assert api_key_for(endpoint, tmp_path) == "sk-file-key"
    monkeypatch.setenv("MY_KEY", "sk-env-key")
    assert api_key_for(endpoint.model_copy(update={"api_key_env": "MY_KEY"}), tmp_path) == "sk-env-key"
    monkeypatch.delenv("MY_KEY")
    assert api_key_for(endpoint.model_copy(update={"api_key_env": "MY_KEY"}), tmp_path) is None
    path.chmod(0o644)
    with pytest.raises(InvalidInput, match="readable by others"):
        read_secrets(tmp_path)


def test_model_for_refuses_without_an_endpoint_or_an_untested_one(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    with pytest.raises(InvalidInput, match="no model endpoint is configured"):
        app.model_for(None)
    endpoint = records.ModelEndpoint(name="gateway", base_url="http://x/v1", model="m", is_default=True)
    app.store.put(endpoint)
    with pytest.raises(InvalidInput, match="has not been tested"):
        app.model_for(None)
    tested = endpoint.model_copy(
        update={"tests": {"tool_calling": records.CapabilityTestResult(status="passed")}}
    )
    app.store.put(tested)
    assert app.model_for(None).name == "m"
    assert app.model_for(endpoint.id).name == "m"
    assert isinstance(app.model_for("mdl_demo"), DemoModel)


def test_cluster_for_a_recording_or_kubectl_and_the_demo_user_is_an_operator(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, user = app.setup_demo()
    assert app.cluster_for(env).context == "demo"
    assert not user.is_admin
    assert app.auth.role(user, env.id) == "operator"
    real = records.Environment(name="real", context="kind-dev")
    assert app.cluster_for(real).context == "kind-dev"
    again_env, again_user = app.setup_demo()  # idempotent
    assert (again_env.id, again_user.id) == (env.id, user.id)
