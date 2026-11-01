"""The capability model, the registry and the gate (design §5.1, §5.2).

A capability is an object: id, purpose, class, systems, a pydantic input model, the permissions it needs
and a `run` function. The registry validates inputs, decides by code whether a user may run a capability
in an environment (the gate), runs it with timing, and turns the result into a `Call` and its `Evidence`.
Nothing the model says reaches the gate; it sees only the user's role and the environment's settings.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from k8sholmes.auth import Auth
from k8sholmes.cluster import Cluster
from k8sholmes.errors import ClusterUnreachable, InvalidInput, K8sHolmesError, NotFound, PermissionRefused
from k8sholmes.evidence import Result, evidence_from_result
from k8sholmes.records import (
    Call,
    CapabilityClass,
    CapabilitySettings,
    Environment,
    Evidence,
    Period,
    Role,
    User,
)
from k8sholmes.store import Store

ROLE_FOR_CLASS: dict[CapabilityClass, Role] = {
    "inspect": "investigator",
    "probe": "operator",
    "change": "operator",
}


@dataclass(frozen=True)
class Context:
    """What a capability's `run` receives besides its inputs."""

    environment: Environment
    cluster: Cluster
    store: Store
    user: User
    settings: CapabilitySettings
    investigation_id: str | None = None
    period: Period = field(default_factory=Period)
    now: datetime | None = None


@dataclass(frozen=True)
class Capability:
    """One entry of the catalog (design §5.1)."""

    id: str
    title: str
    purpose: str
    class_: CapabilityClass
    systems: tuple[str, ...]
    inputs: type[BaseModel]
    permissions: tuple[tuple[str, str], ...]  # (verb, kind)
    run: Callable[[BaseModel, Context], Result]
    origin: str = "built-in"  # or "entry point <name>", "command <file>"
    coverage: tuple[str, ...] = ("observed",)  # the statements it can make, for the catalog page

    def schema(self) -> dict[str, Any]:
        """The JSON schema the model sees as the tool's parameters."""
        schema = self.inputs.model_json_schema()
        schema.pop("title", None)
        return schema

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "purpose": self.purpose,
            "class": self.class_,
            "systems": list(self.systems),
            "permissions": [f"{verb} {kind}" for verb, kind in self.permissions],
            "coverage": list(self.coverage),
            "origin": self.origin,
            "inputs": self.schema(),
        }


@dataclass(frozen=True)
class Refusal:
    reason: str
    kind: str  # "disabled" | "class" | "role" | "permissions" | "unknown"


class Registry:
    """The catalog: lookup, search, validation, the gate, and execution into a call with evidence."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._by_id: dict[str, Capability] = {}
        self.broken: dict[str, str] = {}  # extensions that failed to load: origin -> error
        for cap in capabilities:
            self.add(cap)

    def add(self, cap: Capability) -> None:
        if cap.id in self._by_id:
            raise InvalidInput(
                f"capability id {cap.id!r} is registered twice ({self._by_id[cap.id].origin}, {cap.origin})"
            )
        self._by_id[cap.id] = cap

    def get(self, capability_id: str) -> Capability:
        try:
            return self._by_id[capability_id]
        except KeyError:
            raise NotFound(
                f"capability {capability_id!r} does not exist; `k8sholmes capabilities` lists them"
            ) from None

    def all(self) -> list[Capability]:
        return [self._by_id[k] for k in sorted(self._by_id)]

    def search(self, text: str) -> list[Capability]:
        needle = text.lower().strip()
        return [
            c
            for c in self.all()
            if needle in c.id.lower() or needle in c.title.lower() or needle in c.purpose.lower()
        ]

    # validation and the gate

    @staticmethod
    def validate(cap: Capability, raw: dict[str, Any]) -> BaseModel:
        """Validated inputs, or `InvalidInput` naming the field (guarantee 2)."""
        try:
            return cap.inputs.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            raise InvalidInput(f"{cap.id}: invalid inputs ({problems})") from None

    @staticmethod
    def settings_for(cap: Capability, environment: Environment) -> CapabilitySettings:
        return environment.settings.capabilities.get(cap.id, CapabilitySettings())

    def refusal(self, cap: Capability, user: User, environment: Environment, auth: Auth) -> Refusal | None:
        """Why this user may not run this capability here, or None when they may (design §5.2)."""
        settings = self.settings_for(cap, environment)
        if not settings.enabled or (
            cap.class_ == "probe" and not getattr(settings, "_explicit", True) and False
        ):
            return Refusal(f"{cap.id} is disabled in environment {environment.name}", "disabled")
        if cap.class_ == "probe" and not environment.settings.probes_enabled:
            return Refusal(
                f"{cap.id} is a probe and probes are not enabled in environment {environment.name}", "class"
            )
        if cap.class_ == "change" and environment.settings.changes_authorized_by is None:
            return Refusal(
                f"{cap.id} changes infrastructure and environment {environment.name} has no authorization "
                "path; an admin sets `changes_authorized_by` to `operator_role` or `approval` first "
                "(design §11.3)",
                "class",
            )
        needed = ROLE_FOR_CLASS[cap.class_]
        if not auth.has_role(user, environment.id, needed):
            return Refusal(
                f"{cap.id} needs the {needed} role in environment {environment.name}; "
                f"{user.username} has {auth.role(user, environment.id) or 'no access'}",
                "role",
            )
        missing = [
            f"{verb} {kind}"
            for verb, kind in cap.permissions
            if environment.permissions.get(f"{verb} {kind}") is False
        ]
        if missing:
            return Refusal(
                f"{cap.id} is unavailable in environment {environment.name}: "
                f"the cluster refuses {', '.join(missing)}",
                "permissions",
            )
        return None

    def permitted(self, cap: Capability, user: User, environment: Environment, auth: Auth) -> None:
        refusal = self.refusal(cap, user, environment, auth)
        if refusal is not None:
            raise PermissionRefused(refusal.reason)

    # execution

    def execute(
        self,
        capability_id: str,
        raw_inputs: dict[str, Any],
        ctx: Context,
        auth: Auth,
        *,
        step_id: str | None = None,
    ) -> tuple[Call, Evidence | None]:
        """Validate, gate, run and record. A capability's own failure never raises: it is recorded."""

        def call(status: str, reason: str = "", duration_ms: int = 0) -> Call:
            return Call(
                investigation_id=ctx.investigation_id or "",
                step_id=step_id or "",
                capability_id=capability_id,
                inputs=raw_inputs,
                user_id=ctx.user.id,
                status=status,  # type: ignore[arg-type]
                reason=reason,
                duration_ms=duration_ms,
            )

        try:
            cap = self.get(capability_id)
        except NotFound as exc:
            return call("invalid", str(exc)), None
        refusal = self.refusal(cap, ctx.user, ctx.environment, auth)
        if refusal is not None:
            auth.audit(
                ctx.user.id,
                "call_refused",
                environment_id=ctx.environment.id,
                subject=f"capability:{cap.id}",
                details={"reason": refusal.reason, "kind": refusal.kind},
            )
            return call("refused", refusal.reason), None
        try:
            inputs = self.validate(cap, raw_inputs)
        except InvalidInput as exc:
            return call("invalid", str(exc)), None
        started = time.monotonic()
        try:
            result = cap.run(inputs, ctx)
            status = "completed"
            reason = ""
        except (NotFound, PermissionRefused, ClusterUnreachable, K8sHolmesError) as exc:
            result = Result(
                summary=f"{cap.id} failed: {exc}",
                data=None,
                coverage="cannot_answer"
                if isinstance(exc, PermissionRefused | ClusterUnreachable)
                else "no_data",
                coverage_reason=str(exc),
                error=str(exc),
            )
            status = "failed"
            reason = str(exc)
        duration_ms = int((time.monotonic() - started) * 1000)
        made = call(status, reason, duration_ms)
        evidence = evidence_from_result(
            result,
            source=cap.id,
            systems=list(cap.systems),
            query=inputs.model_dump(mode="json"),
            environment_id=ctx.environment.id,
            investigation_id=ctx.investigation_id,
            step_id=step_id,
            call_id=made.id,
            max_bytes=ctx.settings.max_log_bytes,
        )
        return made.model_copy(update={"evidence_id": evidence.id}), evidence
