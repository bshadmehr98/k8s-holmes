"""Typed errors. Every message says what happened and what to do."""

from __future__ import annotations


class K8sHolmesError(Exception):
    """The base of every error k8sholmes raises on purpose."""


class ClusterUnreachable(K8sHolmesError):
    """The cluster could not be asked: no kubeconfig, no context, a refused connection or a timeout."""


class InvalidCase(K8sHolmesError):
    """A case file that can't be read: a file missing, broken, or from a format this version can't open."""


class ToolMissing(K8sHolmesError):
    """A command this needs (kubectl) is not on the PATH; the message says how to install it."""
