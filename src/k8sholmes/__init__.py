"""K8sHolmes: a self-hosted platform for investigating Kubernetes environments (design/current.md).

    from k8sholmes import App
    app = App.open()                                   # ~/.k8sholmes, or $K8SHOLMES_HOME
    env, user = app.setup_demo()                       # the recorded demo environment, offline investigator
    inv = app.engine.start(environment=env, question="Why is api restarting?", user=user)
    inv = app.engine.run(inv.id, user)                 # then store.find(Finding, {"investigation_id": …})

`design/current.md` §19 says what is built.
"""

from k8sholmes.app import App
from k8sholmes.engine import Engine
from k8sholmes.errors import (
    ClusterUnreachable,
    InvalidInput,
    InvestigationStateError,
    K8sHolmesError,
    ModelFailure,
    NotFound,
    PermissionRefused,
    StoreVersionError,
    ToolMissing,
)
from k8sholmes.store import Store

__version__ = "0.1.0"

__all__ = [
    "App",
    "ClusterUnreachable",
    "Engine",
    "InvalidInput",
    "InvestigationStateError",
    "K8sHolmesError",
    "ModelFailure",
    "NotFound",
    "PermissionRefused",
    "Store",
    "StoreVersionError",
    "ToolMissing",
    "__version__",
]
