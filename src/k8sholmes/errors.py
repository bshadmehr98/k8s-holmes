"""Typed errors. Every message says what happened and what to do (design §16.2 tells retryable apart)."""

from __future__ import annotations


class K8sHolmesError(Exception):
    """The base of every error K8sHolmes raises on purpose."""


class NotFound(K8sHolmesError):
    """A record, resource, environment, capability or user that does not exist."""


class InvalidInput(K8sHolmesError):
    """Input that fails validation; the message names the field."""


class PermissionRefused(K8sHolmesError):
    """The gate said no: the user's role, the environment's settings or the capability's class forbid it."""


class ClusterUnreachable(K8sHolmesError):
    """The cluster could not be asked: no kubectl, no context, a refused connection or a timeout."""


class ToolMissing(K8sHolmesError):
    """A program this needs (kubectl) is not on the PATH; the message says how to install it."""


class ModelFailure(K8sHolmesError):
    """A model call failed; `retryable` says whether trying again on the same endpoint makes sense."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class StoreVersionError(K8sHolmesError):
    """The store was written by a newer K8sHolmes; the message says which version opens it."""


class InvestigationStateError(K8sHolmesError):
    """An action the investigation's status does not allow (resume a running one, follow up a queued one)."""
