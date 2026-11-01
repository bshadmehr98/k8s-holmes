"""k8sholmes: a detective for Kubernetes clusters (design/current.md; design 0001 is proposed).

Nothing is built yet: the first design waits for the owner's acceptance. The public API will be listed
here with an explicit ``__all__`` as it is built.
"""

from k8sholmes.errors import ClusterUnreachable, InvalidCase, K8sHolmesError, ToolMissing

__version__ = "0.1.0"

__all__ = [
    "ClusterUnreachable",
    "InvalidCase",
    "K8sHolmesError",
    "ToolMissing",
    "__version__",
]
