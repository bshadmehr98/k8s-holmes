"""Every record of design §2, as pydantic models with `extra="forbid"`.

These are the shapes of the store's rows, the JSON API and the export. Times are UTC; durations are
integer milliseconds; ids come from `new_id`, never from `hash()`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def now() -> datetime:
    """The one clock (design §2)."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """A short, URL-safe, stable id such as `inv_7f3a9c1d`."""
    return f"{prefix}_{secrets.token_hex(4)}"


class Record(BaseModel):
    """Base of every record: unknown fields are refused, so a stale client fails loudly."""

    model_config = ConfigDict(extra="forbid")


# Shared shapes


class ResourceRef(Record):
    kind: str
    namespace: str | None = None
    name: str

    def key(self) -> str:
        return f"{self.kind}/{self.namespace or '-'}/{self.name}"


class Scope(Record):
    namespaces: list[str] = []  # empty: every namespace the permissions allow
    resources: list[ResourceRef] = []


class Period(Record):
    start: datetime | None = None
    end: datetime | None = None
    rule: str | None = None  # "the last 24 hours", for saved investigations


Coverage = Literal["observed", "observed_absent", "no_data", "cannot_answer"]
Basis = Literal["defined", "observed", "inferred"]
CapabilityClass = Literal["inspect", "probe", "change"]
Role = Literal["viewer", "investigator", "operator", "admin"]
ROLE_ORDER: dict[str, int] = {"viewer": 0, "investigator": 1, "operator": 2, "admin": 3}


class Revision(Record):
    at: datetime
    author: str  # "model:<name>" or "user:<id>"
    change: str  # what changed, in words
    before: dict[str, Any] = {}


# Environments (design §4)


class CapabilitySettings(Record):
    enabled: bool = True
    namespaces: list[str] = []  # empty: the scope's namespaces
    max_items: int = 500
    max_log_bytes: int = 262_144
    timeout_ms: int = 20_000


class RetentionPolicy(Record):
    short_evidence_days: int = 30
    investigation_days: int = 365


class EnvironmentSettings(Record):
    probes_enabled: bool = False
    changes_authorized_by: Literal["operator_role", "approval"] | None = None
    capabilities: dict[str, CapabilitySettings] = {}
    default_model_id: str | None = None
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)


class Environment(Record):
    id: str = Field(default_factory=lambda: new_id("env"))
    name: str
    context: str
    kubeconfig: str | None = None  # a path, read by kubectl; never copied
    recording: str | None = None  # a recorded cluster folder instead of kubectl (the demo, tests)
    created_at: datetime = Field(default_factory=now)
    settings: EnvironmentSettings = Field(default_factory=EnvironmentSettings)
    permissions: dict[str, bool] = {}  # "verb kind" -> allowed, from the last connection check (design §4.1)
    connected_at: datetime | None = None
    server_version: str | None = None


class Relationship(Record):
    source: ResourceRef
    target: ResourceRef
    kind: Literal[
        "owns", "selects", "routes_to", "mounts", "references", "targets", "policy_selects", "mentions"
    ]
    basis: Basis
    detail: str = ""


class InventoryItem(Record):
    ref: ResourceRef
    labels: dict[str, str] = {}
    owner: ResourceRef | None = None
    created_at: datetime | None = None
    summary: dict[str, Any] = {}  # the fields the capabilities read


class Inventory(Record):
    id: str = Field(default_factory=lambda: new_id("ivt"))
    environment_id: str
    taken_at: datetime = Field(default_factory=now)
    namespaces: list[str] = []
    items: list[InventoryItem] = []
    relationships: list[Relationship] = []
    readable_kinds: list[str] = []
    unreadable: dict[str, str] = {}  # kind -> why
    truncated: list[str] = []


# Users, sessions, audit (design §14)


class Grant(Record):
    environment_id: str
    role: Role


class User(Record):
    id: str = Field(default_factory=lambda: new_id("usr"))
    username: str
    password_hash: str
    is_admin: bool = False
    grants: list[Grant] = []
    created_at: datetime = Field(default_factory=now)


class Session(Record):
    id: str = Field(default_factory=lambda: new_id("ses"))
    token_hash: str
    user_id: str
    created_at: datetime = Field(default_factory=now)
    expires_at: datetime


class AuditEntry(Record):
    id: str = Field(default_factory=lambda: new_id("aud"))
    at: datetime = Field(default_factory=now)
    user_id: str | None
    action: str
    environment_id: str | None = None
    subject: str | None = None  # "finding:fnd_…"
    details: dict[str, Any] = {}


# Investigations (design §6)


InvestigationStatus = Literal[
    "queued", "running", "completed", "partial", "cancelled", "failed", "interrupted"
]
Phase = Literal["framing", "discovery", "gathering", "concluding", "follow_up"]


class Budgets(Record):
    steps: int = 24
    calls_per_step: int = 8
    tokens: int = 400_000
    wall_ms: int = 900_000


class Usage(Record):
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    price: float | None = None


class Framing(Record):
    question: str
    scope: Scope = Field(default_factory=Scope)
    period: Period = Field(default_factory=Period)
    hypotheses: list[str] = []
    intended_capabilities: list[str] = []


class Investigation(Record):
    id: str = Field(default_factory=lambda: new_id("inv"))
    environment_id: str
    question: str
    scope: Scope = Field(default_factory=Scope)
    period: Period = Field(default_factory=Period)
    status: InvestigationStatus = "queued"
    created_by: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    model_endpoint_id: str | None = None
    model_name: str | None = None
    budgets: Budgets = Field(default_factory=Budgets)
    current_step: int = 0
    framing: Framing | None = None
    limitations: list[str] = []
    knowledge_in_scope: list[str] = []
    exceptions_in_scope: list[str] = []
    saved_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    answer: str | None = None  # the one-paragraph answer shown first (design §15.2)


class ToolCall(Record):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class Message(Record):
    """One entry of an investigation's thread, kept so a resume replays exactly what the model saw."""

    id: str = Field(default_factory=lambda: new_id("msg"))
    investigation_id: str
    seq: int
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = []
    tool_call_id: str | None = None
    author: str  # "user:<id>" or "model:<name>"
    at: datetime = Field(default_factory=now)
    round: int = 0  # 0: the first run; n: the n-th follow-up


class Step(Record):
    id: str = Field(default_factory=lambda: new_id("stp"))
    investigation_id: str
    seq: int
    phase: Phase
    status: Literal["running", "completed", "interrupted", "failed"] = "running"
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    summary: str = ""


class Call(Record):
    id: str = Field(default_factory=lambda: new_id("cal"))
    investigation_id: str
    step_id: str
    capability_id: str
    inputs: dict[str, Any] = {}
    status: Literal["completed", "refused", "failed", "invalid"]
    reason: str = ""  # the refusal, the validation error or the failure
    duration_ms: int = 0
    evidence_id: str | None = None
    user_id: str
    at: datetime = Field(default_factory=now)


class Evidence(Record):
    id: str = Field(default_factory=lambda: new_id("ev"))
    investigation_id: str | None
    environment_id: str
    step_id: str | None = None
    call_id: str | None = None
    source: str  # the capability id
    systems: list[str] = []
    query: dict[str, Any] = {}
    collected_at: datetime = Field(default_factory=now)
    data: Any = None
    sha256: str = ""
    truncated: bool = False
    coverage: Coverage
    coverage_reason: str = ""
    redactions: int = 0
    retention: Literal["investigation", "pinned", "short"] = "investigation"
    error: str | None = None
    summary: str = ""


# Findings (design §8)


FindingKind = Literal["observation", "hypothesis", "conclusion"]
FindingState = Literal["open", "acknowledged", "accepted_risk", "fixed", "verified"]


class Finding(Record):
    id: str = Field(default_factory=lambda: new_id("fnd"))
    investigation_id: str
    environment_id: str
    title: str
    explanation: str
    kind: FindingKind = "observation"
    condition: str  # "replicas.single", "custom.<slug>"
    resources: list[ResourceRef] = []
    period: Period = Field(default_factory=Period)
    evidence_for: list[str] = []
    evidence_against: list[str] = []
    uncertainty: str = ""
    missing_information: list[str] = []
    next_steps: list[str] = []
    owner: str | None = None
    state: FindingState = "open"
    author: str  # "model:<name>" or "user:<id>"
    knowledge_used: list[str] = []
    exception_id: str | None = None
    exception_note: str | None = None  # "an exception expired on …"
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    revisions: list[Revision] = []

    def key(self) -> str:
        """The de-duplication key of design §8.3."""
        return f"{self.environment_id}|{self.condition}|" + ",".join(sorted(r.key() for r in self.resources))


# Knowledge and exceptions (design §9)


class AcceptedException(Record):
    id: str = Field(default_factory=lambda: new_id("exc"))
    environment_id: str
    condition: str
    scope: ResourceRef  # exact; name may be empty when applies_to == "namespace"
    applies_to: Literal["resource", "namespace"] = "resource"
    explanation: str
    approved_by: str
    approved_at: datetime = Field(default_factory=now)
    review_at: datetime | None = None
    expires_at: datetime | None = None
    reconsider_when: str = ""
    triggers: list[Literal["replicas_change", "owner_change", "image_change", "resource_deleted"]] = []
    snapshot: dict[str, Any] = {}  # the values the triggers compare against
    revisions: list[Revision] = []
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: str = ""
    finding_id: str | None = None  # the finding it was made from


KnowledgeKind = Literal[
    "traffic", "criticality", "ownership", "maintenance", "constraint", "dependency", "lesson"
]


class Knowledge(Record):
    id: str = Field(default_factory=lambda: new_id("kno"))
    environment_id: str
    kind: KnowledgeKind
    scope: ResourceRef | None = None  # None: the whole environment
    text: str
    author: str
    source: str = "user"  # or "investigation <id>"
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    revisions: list[Revision] = []


# Models (design §13)


class ModelLimits(Record):
    max_tokens: int = 4096
    timeout_ms: int = 120_000
    tokens_per_investigation: int = 400_000
    tokens_per_day: int = 4_000_000


class ModelPrice(Record):
    input_per_million: float
    output_per_million: float


TestStatus = Literal["passed", "failed", "unsupported"]


class CapabilityTestResult(Record):
    status: TestStatus
    detail: str = ""
    at: datetime = Field(default_factory=now)


class ModelEndpoint(Record):
    id: str = Field(default_factory=lambda: new_id("mdl"))
    name: str
    base_url: str
    model: str
    api_key_env: str | None = None  # else the key is in the secrets file under this endpoint's id
    headers: dict[str, str] = {}
    limits: ModelLimits = Field(default_factory=ModelLimits)
    price: ModelPrice | None = None
    tests: dict[str, CapabilityTestResult] = {}
    is_default: bool = False
    created_at: datetime = Field(default_factory=now)


class ModelCall(Record):
    id: str = Field(default_factory=lambda: new_id("mc"))
    investigation_id: str | None
    endpoint_id: str
    model: str
    at: datetime = Field(default_factory=now)
    phase: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    status: Literal["ok", "retried", "failed"] = "ok"
    error: str | None = None
