"""Every Model implementation keeps the contract (design §13.1); the real client is checked in the integration suite."""

from k8sholmes.testing import ScriptedModel, check_model
from k8sholmes.testing.demo_model import DemoModel
from k8sholmes.testing.model import text


def test_scripted_and_demo_models() -> None:
    check_model(ScriptedModel([text("ok")]))
    check_model(DemoModel())
