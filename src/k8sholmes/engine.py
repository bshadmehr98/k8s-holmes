"""The investigation engine (design §6): framing, discovery, gathering, concluding, follow-up.

Every step is committed as it completes (§16.2). The model sees the system prompt, the framing, what was
discovered, the team's knowledge marked as such, and the thread; it calls capabilities, and the registry's
gate decides by code whether each may run. The conclusion is validated against the evidence (§6.3).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import timedelta
from importlib import resources
from typing import Any, cast

from k8sholmes import findings as findings_mod
from k8sholmes import knowledge
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Capability, Context, Registry
from k8sholmes.cluster import Cluster
from k8sholmes.errors import InvestigationStateError, ModelFailure, NotFound
from k8sholmes.models import Model, Reply, tool_definition
from k8sholmes.records import (
    AcceptedException,
    Budgets,
    Call,
    Environment,
    Evidence,
    Finding,
    Framing,
    Investigation,
    Knowledge,
    Message,
    ModelCall,
    Period,
    Phase,
    ResourceRef,
    Scope,
    Step,
    ToolCall,
    User,
    now,
)
from k8sholmes.store import Store

MODEL_DATA_CHARS = 12_000  # how much of a result's data the model sees per tool message
RETRY_BACKOFF_S = (1.0, 3.0)
EventHook = Callable[[str, dict[str, Any]], None]


def _load(name: str) -> str:
    return resources.files("k8sholmes.prompts").joinpath(name).read_text()


SYSTEM_PROMPT = _load("system.md")
FRAMING_SCHEMA: dict[str, Any] = json.loads(_load("framing.schema.json"))
CONCLUSION_SCHEMA: dict[str, Any] = json.loads(_load("conclusion.schema.json"))


class Engine:
    """Runs investigations against a store, a registry, a cluster per environment and a model per endpoint."""

    def __init__(
        self,
        store: Store,
        auth: Auth,
        registry: Registry,
        *,
        cluster_for: Callable[[Environment], Cluster],
        model_for: Callable[[Investigation], Model],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.auth = auth
        self.registry = registry
        self.cluster_for = cluster_for
        self.model_for = model_for
        self.sleep = sleep
        self.on_event: EventHook | None = None

    # starting and control

    def start(
        self,
        *,
        environment: Environment,
        question: str,
        user: User,
        scope: Scope | None = None,
        period: Period | None = None,
        model_endpoint_id: str | None = None,
        budgets: Budgets | None = None,
        saved_id: str | None = None,
    ) -> Investigation:
        self.auth.require(user, environment.id, "investigator")
        question = question.strip()
        if not question:
            raise InvestigationStateError("the question is empty")
        investigation = Investigation(
            environment_id=environment.id,
            question=question,
            scope=scope or Scope(),
            period=period or Period(),
            created_by=user.id,
            model_endpoint_id=model_endpoint_id or environment.settings.default_model_id,
            budgets=budgets or Budgets(),
            saved_id=saved_id,
        )
        self.store.put(investigation)
        self.store.put(
            Message(
                investigation_id=investigation.id,
                seq=0,
                role="user",
                content=question,
                author=f"user:{user.id}",
            )
        )
        self.auth.audit(
            user.id,
            "investigation_started",
            environment_id=environment.id,
            subject=f"investigation:{investigation.id}",
        )
        return investigation

    def cancel(self, investigation_id: str, user: User) -> Investigation:
        inv = self.store.get(Investigation, investigation_id)
        self.auth.require(user, inv.environment_id, "investigator")
        if inv.status not in ("queued", "running"):
            raise InvestigationStateError(
                f"investigation {inv.id} is {inv.status}; only a queued or running one is cancelled"
            )
        inv = self._update(inv, status="cancelled")
        self.auth.audit(
            user.id,
            "investigation_cancelled",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
        )
        return inv

    def resume(self, investigation_id: str, user: User) -> Investigation:
        inv = self.store.get(Investigation, investigation_id)
        self.auth.require(user, inv.environment_id, "investigator")
        if inv.status not in ("cancelled", "interrupted", "partial", "failed"):
            raise InvestigationStateError(
                f"investigation {inv.id} is {inv.status}; "
                "only a cancelled, interrupted, partial or failed one is resumed"
            )
        self.auth.audit(
            user.id,
            "investigation_resumed",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
        )
        return self.run(investigation_id, user)

    def follow_up(self, investigation_id: str, user: User, message: str) -> Investigation:
        """A user's message, context or requested check: another round of gathering and concluding (§6.1)."""
        inv = self.store.get(Investigation, investigation_id)
        self.auth.require(user, inv.environment_id, "investigator")
        if inv.status in ("queued", "running"):
            # a redirect: the loop picks the message up on its next turn
            self._append(inv, "user", message, author=f"user:{user.id}", round_=self._round(inv))
            return inv
        round_ = self._round(inv) + 1
        self._append(inv, "user", message, author=f"user:{user.id}", round_=round_)
        self.auth.audit(
            user.id,
            "investigation_follow_up",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
        )
        inv = self._update(inv, status="running")
        return self._run_phases(inv, user, ("gathering", "concluding"), follow_up=True)

    def mark_interrupted(self) -> list[Investigation]:
        """At server start: whatever was running when the process died is interrupted (design §16.2)."""
        marked: list[Investigation] = []
        for inv in self.store.find(Investigation, {"status": "running"}):
            for step in self.store.find(Step, {"investigation_id": inv.id, "status": "running"}):
                self.store.put(step.model_copy(update={"status": "interrupted", "finished_at": now()}))
            marked.append(self._update(inv, status="interrupted"))
        return marked

    # running

    def run(self, investigation_id: str, user: User) -> Investigation:
        """Run from where the investigation stands to its end. Synchronous; the caller chooses the thread."""
        inv = self.store.get(Investigation, investigation_id)
        if inv.status not in ("queued", "cancelled", "interrupted", "partial", "failed"):
            raise InvestigationStateError(f"investigation {inv.id} is {inv.status}")
        inv = self._update(inv, status="running")
        phases: list[Phase] = []
        if inv.framing is None:
            phases.append("framing")
        if not self.store.find(
            Step, {"investigation_id": inv.id, "phase": "discovery", "status": "completed"}
        ):
            phases.append("discovery")
        phases.append("gathering")
        phases.append("concluding")
        return self._run_phases(inv, user, tuple(phases))

    def _run_phases(
        self, inv: Investigation, user: User, phases: tuple[Phase, ...], *, follow_up: bool = False
    ) -> Investigation:
        model = self.model_for(inv)
        environment = self.store.get(Environment, inv.environment_id)
        cluster = self.cluster_for(environment)
        started = time.monotonic()
        try:
            for phase in phases:
                inv = self.store.get(Investigation, inv.id)
                if inv.status == "cancelled":
                    return inv
                if phase == "framing":
                    inv = self._framing(inv, model, user)
                elif phase == "discovery":
                    inv = self._discovery(inv, environment, cluster, user)
                elif phase == "gathering":
                    inv = self._gathering(inv, model, environment, cluster, user, started)
                elif phase == "concluding":
                    inv = self._concluding(inv, model, user, follow_up=follow_up)
        except ModelFailure as exc:
            inv = self._update(
                inv, status="failed", limitations=[*inv.limitations, f"the model failed: {exc}"]
            )
            self._emit("failed", {"investigation_id": inv.id, "error": str(exc)})
        return inv

    # phases

    def _framing(self, inv: Investigation, model: Model, user: User) -> Investigation:
        step = self._begin_step(inv, "framing")
        period_text = inv.period.rule or (inv.period.start.isoformat() if inv.period.start else "not given")
        prompt = (
            f"Question: {inv.question}\n"
            f"User's scope: namespaces {inv.scope.namespaces or 'not given'}; "
            f"resources {[r.key() for r in inv.scope.resources] or 'none named'}.\n"
            f"User's period: {period_text}.\n"
            "Frame the investigation: restate the question, name the namespaces and resources in scope, "
            "the period in hours, the hypotheses to test, and the capabilities you intend to use first."
        )
        reply = self._call_model(
            inv,
            model,
            "framing",
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            schema=FRAMING_SCHEMA,
        )
        payload = _json_object(reply.content)
        hours = float(payload.get("period_hours") or 24)
        period = Period(
            start=now() - timedelta(hours=hours),
            end=now(),
            rule=inv.period.rule or f"the last {hours:g} hours",
        )
        resources_raw = cast(list[dict[str, Any]], payload.get("resources") or [])
        framing = Framing(
            question=str(payload.get("question") or inv.question),
            scope=Scope(
                namespaces=[
                    str(n) for n in cast(list[Any], payload.get("namespaces") or inv.scope.namespaces)
                ],
                resources=[
                    ResourceRef(
                        kind=str(r.get("kind", "")), namespace=r.get("namespace"), name=str(r.get("name", ""))
                    )
                    for r in resources_raw
                    if r.get("name")
                ]
                or inv.scope.resources,
            ),
            period=period,
            hypotheses=[str(h) for h in cast(list[Any], payload.get("hypotheses") or [])],
            intended_capabilities=[
                str(c) for c in cast(list[Any], payload.get("intended_capabilities") or [])
            ],
        )
        inv = self._update(inv, framing=framing)
        self._end_step(inv, step, f"framed: {framing.question}")
        return inv

    def _discovery(
        self, inv: Investigation, environment: Environment, cluster: Cluster, user: User
    ) -> Investigation:
        step = self._begin_step(inv, "discovery")
        framing = inv.framing or Framing(question=inv.question)
        notes = knowledge.notes_in_scope(
            self.store, environment.id, framing.scope.resources, framing.scope.namespaces
        )
        exceptions = knowledge.exceptions_in_scope(
            self.store, environment.id, framing.scope.resources, framing.scope.namespaces
        )
        ctx = self._context(inv, environment, cluster, user)
        call, evidence = self.registry.execute(
            "k8s.inventory", {"namespaces": framing.scope.namespaces}, ctx, self.auth, step_id=step.id
        )
        with self.store.transaction():
            self.store.put(call)
            if evidence is not None:
                self.store.put(evidence)
        inv = self._update(
            inv, knowledge_in_scope=[n.id for n in notes], exceptions_in_scope=[e.id for e in exceptions]
        )
        available = [c.id for c in self._available(environment, user)]
        self._end_step(
            inv,
            step,
            f"{len(available)} capabilities available; {len(notes)} notes and {len(exceptions)} exceptions",
        )
        return inv

    def _gathering(
        self,
        inv: Investigation,
        model: Model,
        environment: Environment,
        cluster: Cluster,
        user: User,
        started: float,
    ) -> Investigation:
        available = self._available(environment, user)
        tools = [tool_definition(c.id, f"{c.title}. {c.purpose}", c.schema()) for c in available]
        ctx = self._context(inv, environment, cluster, user)
        while True:
            inv = self.store.get(Investigation, inv.id)
            if inv.status == "cancelled":
                return inv
            limitation = self._budget_hit(inv, started)
            if limitation:
                return self._update(inv, limitations=[*inv.limitations, limitation])
            step = self._begin_step(inv, "gathering")
            reply = self._call_model(
                inv, model, "gathering", self._messages(inv, environment, available), tools=tools
            )
            round_ = self._round(inv)
            self._append(
                inv,
                "assistant",
                reply.content,
                author=f"model:{model.name}",
                tool_calls=reply.tool_calls,
                round_=round_,
            )
            if not reply.tool_calls:
                self._end_step(inv, step, "the model has what it needs")
                return self.store.get(Investigation, inv.id)
            summaries: list[str] = []
            for tool_call in reply.tool_calls[: inv.budgets.calls_per_step]:
                call, evidence = self.registry.execute(
                    tool_call.name, tool_call.arguments, ctx, self.auth, step_id=step.id
                )
                with self.store.transaction():
                    self.store.put(call)
                    if evidence is not None:
                        self.store.put(evidence)
                    self._append(
                        inv,
                        "tool",
                        self._tool_message(call, evidence),
                        author=f"capability:{tool_call.name}",
                        tool_call_id=tool_call.id,
                        round_=round_,
                    )
                summaries.append(
                    f"{tool_call.name}: {call.status}"
                    + (f" ({evidence.coverage})" if evidence else f" ({call.reason[:80]})")
                )
                if call.status == "refused":
                    inv = self._update(
                        self.store.get(Investigation, inv.id),
                        limitations=[*inv.limitations, f"refused: {call.reason}"],
                    )
                self._emit("call", {"investigation_id": inv.id, "call": call.model_dump(mode="json")})
            for skipped in reply.tool_calls[inv.budgets.calls_per_step :]:
                self._append(
                    inv,
                    "tool",
                    json.dumps({"error": "not run: the step's call budget was spent"}),
                    author="engine",
                    tool_call_id=skipped.id,
                    round_=round_,
                )
            self._end_step(inv, step, "; ".join(summaries))

    def _concluding(self, inv: Investigation, model: Model, user: User, *, follow_up: bool) -> Investigation:
        step = self._begin_step(inv, "concluding")
        environment = self.store.get(Environment, inv.environment_id)
        evidence = {e.id: e for e in self.store.find(Evidence, {"investigation_id": inv.id})}
        knowledge_ids = set(inv.knowledge_in_scope) | set(inv.exceptions_in_scope)
        messages = [
            *self._messages(inv, environment, self._available(environment, user)),
            {
                "role": "user",
                "content": (
                    "Conclude now. Cite evidence ids for every finding; state limitations where coverage "
                    "does not allow a claim; list the knowledge notes you used."
                ),
            },
        ]
        validated = findings_mod.Validated()
        for attempt in range(2):
            reply = self._call_model(inv, model, "concluding", messages, schema=CONCLUSION_SCHEMA)
            validated = findings_mod.validate_conclusion(_json_object(reply.content), evidence, knowledge_ids)
            if not validated.problems or attempt == 1:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": reply.content},
                {
                    "role": "user",
                    "content": "Repair your conclusion. Problems:\n- " + "\n- ".join(validated.problems),
                },
            ]
        exceptions = [
            self.store.get(AcceptedException, x)
            for x in inv.exceptions_in_scope
            if self._exists(AcceptedException, x)
        ]
        existing = self.store.find(Finding, {"investigation_id": inv.id}) if follow_up else []
        stored = findings_mod.store_findings(
            self.store, inv, validated, author=f"model:{model.name}", exceptions=exceptions, existing=existing
        )
        limitations = [*inv.limitations, *validated.limitations]
        if validated.problems:
            limitations.append(
                "the model's conclusion still had problems after one repair: " + "; ".join(validated.problems)
            )
        status = (
            "partial"
            if limitations
            or any(c.status == "refused" for c in self.store.find(Call, {"investigation_id": inv.id}))
            else "completed"
        )
        inv = self._update(inv, status=status, answer=validated.answer, limitations=limitations)
        self._end_step(inv, step, f"{len(stored)} findings, {len(validated.limitations)} limitations")
        self._emit("concluded", {"investigation_id": inv.id, "status": status})
        return inv

    # context the model sees

    def _available(self, environment: Environment, user: User) -> list[Capability]:
        return [
            c for c in self.registry.all() if self.registry.refusal(c, user, environment, self.auth) is None
        ]

    def _messages(
        self, inv: Investigation, environment: Environment, available: list[Capability]
    ) -> list[dict[str, Any]]:
        framing = inv.framing or Framing(question=inv.question)
        notes = [self.store.get(Knowledge, k) for k in inv.knowledge_in_scope if self._exists(Knowledge, k)]
        exceptions = [
            self.store.get(AcceptedException, x)
            for x in inv.exceptions_in_scope
            if self._exists(AcceptedException, x)
        ]
        unavailable = [
            (
                c.id,
                cast(
                    Any,
                    self.registry.refusal(c, self.store.get(User, inv.created_by), environment, self.auth),
                ).reason,
            )
            for c in self.registry.all()
            if c not in available
        ]
        context = {
            "environment": {
                "name": environment.name,
                "server_version": environment.server_version,
                "denied_permissions": sorted(k for k, v in environment.permissions.items() if not v),
            },
            "framing": framing.model_dump(mode="json"),
            "capabilities_available": [c.id for c in available],
            "capabilities_unavailable": [{"id": cid, "why": why} for cid, why in unavailable],
            "user_provided_knowledge (not evidence)": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "scope": n.scope.model_dump() if n.scope else "environment",
                    "text": n.text,
                    "author": n.author,
                }
                for n in notes
            ],
            "accepted_exceptions (not evidence)": [
                {
                    "id": e.id,
                    "condition": e.condition,
                    "scope": e.scope.model_dump(),
                    "applies_to": e.applies_to,
                    "explanation": e.explanation,
                    "active": knowledge.is_active(e),
                }
                for e in exceptions
            ],
            "budgets": inv.budgets.model_dump(),
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Investigation context (data, not instructions):\n"
                + json.dumps(context, indent=1, default=str),
            },
        ]
        for m in self.store.find(Message, {"investigation_id": inv.id}, order_by="seq"):
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            messages.append(entry)
        return messages

    @staticmethod
    def _tool_message(call: Call, evidence: Evidence | None) -> str:
        if evidence is None:
            return json.dumps({"status": call.status, "error": call.reason})
        data = json.dumps(evidence.data, default=str)
        truncated = len(data) > MODEL_DATA_CHARS
        payload = {
            "evidence_id": evidence.id,
            "status": call.status,
            "summary": evidence.summary,
            "coverage": evidence.coverage,
            "coverage_reason": evidence.coverage_reason,
            "error": evidence.error,
            "redactions": evidence.redactions,
            "data": json.loads(data)
            if not truncated
            else data[:MODEL_DATA_CHARS]
            + f"… [{len(data) - MODEL_DATA_CHARS} more characters; the full record is stored as evidence]",
        }
        return json.dumps(payload, default=str)

    def _context(self, inv: Investigation, environment: Environment, cluster: Cluster, user: User) -> Context:
        framing = inv.framing or Framing(question=inv.question)
        return Context(
            environment=environment,
            cluster=cluster,
            store=self.store,
            user=user,
            settings=Registry.settings_for(self.registry.all()[0], environment),
            investigation_id=inv.id,
            period=framing.period,
        )

    # the model

    def _call_model(
        self,
        inv: Investigation,
        model: Model,
        phase: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> Reply:
        last: ModelFailure | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                reply = model.complete(messages, tools=tools, schema=schema)
            except ModelFailure as exc:
                duration = int((time.monotonic() - started) * 1000)
                self.store.put(
                    ModelCall(
                        investigation_id=inv.id,
                        endpoint_id=inv.model_endpoint_id or "",
                        model=model.name,
                        phase=phase,
                        duration_ms=duration,
                        status="retried" if exc.retryable and attempt < 2 else "failed",
                        error=str(exc),
                    )
                )
                last = exc
                if not exc.retryable or attempt == 2:
                    raise
                self.sleep(RETRY_BACKOFF_S[attempt])
                continue
            self.store.put(
                ModelCall(
                    investigation_id=inv.id,
                    endpoint_id=inv.model_endpoint_id or "",
                    model=model.name,
                    phase=phase,
                    prompt_tokens=reply.prompt_tokens,
                    completion_tokens=reply.completion_tokens,
                    duration_ms=reply.duration_ms,
                )
            )
            usage = inv.usage.model_copy(
                update={
                    "model_calls": inv.usage.model_calls + 1,
                    "prompt_tokens": inv.usage.prompt_tokens + reply.prompt_tokens,
                    "completion_tokens": inv.usage.completion_tokens + reply.completion_tokens,
                    "duration_ms": inv.usage.duration_ms + reply.duration_ms,
                }
            )
            inv.usage = usage
            self._update(inv, usage=usage, model_name=model.name)
            self._emit(
                "model_call",
                {
                    "investigation_id": inv.id,
                    "phase": phase,
                    "tokens": reply.prompt_tokens + reply.completion_tokens,
                },
            )
            return reply
        raise last or ModelFailure("the model failed", retryable=False)

    # bookkeeping

    def _budget_hit(self, inv: Investigation, started: float) -> str | None:
        if inv.current_step >= inv.budgets.steps:
            return f"the step budget ({inv.budgets.steps}) was spent before the model concluded"
        if inv.usage.prompt_tokens + inv.usage.completion_tokens >= inv.budgets.tokens:
            return f"the token budget ({inv.budgets.tokens}) was spent"
        if (time.monotonic() - started) * 1000 >= inv.budgets.wall_ms:
            return f"the time budget ({inv.budgets.wall_ms} ms) was spent"
        return None

    def _begin_step(self, inv: Investigation, phase: Phase) -> Step:
        step = Step(investigation_id=inv.id, seq=inv.current_step + 1, phase=phase)
        with self.store.transaction():
            self.store.put(step)
            self._update(inv, current_step=step.seq)
        self._emit("step", {"investigation_id": inv.id, "step": step.model_dump(mode="json")})
        return step

    def _end_step(self, inv: Investigation, step: Step, summary: str) -> None:
        self.store.put(
            step.model_copy(update={"status": "completed", "finished_at": now(), "summary": summary[:500]})
        )
        self._emit(
            "step",
            {
                "investigation_id": inv.id,
                "step": {**step.model_dump(mode="json"), "status": "completed", "summary": summary[:500]},
            },
        )

    def _append(
        self,
        inv: Investigation,
        role: str,
        content: str,
        *,
        author: str,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
        round_: int = 0,
    ) -> Message:
        seq = self.store.count(Message, {"investigation_id": inv.id})
        message = Message(
            investigation_id=inv.id,
            seq=seq,
            role=cast(Any, role),
            content=content,
            tool_calls=tool_calls or [],
            tool_call_id=tool_call_id,
            author=author,
            round=round_,
        )
        self.store.put(message)
        return message

    def _round(self, inv: Investigation) -> int:
        messages = self.store.find(Message, {"investigation_id": inv.id})
        return max((m.round for m in messages), default=0)

    def _update(self, inv: Investigation, **fields: Any) -> Investigation:
        fresh = self.store.get(Investigation, inv.id)
        updated = fresh.model_copy(update={**fields, "updated_at": now()})
        self.store.put(updated)
        if "status" in fields:
            self._emit("status", {"investigation_id": inv.id, "status": updated.status})
        return updated

    def _exists(self, cls: type[Any], record_id: str) -> bool:
        try:
            self.store.get(cls, record_id)
        except NotFound:
            return False
        return True

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(kind, payload)


def _json_object(text: str) -> dict[str, Any]:
    """The model's structured reply as a dict; tolerant of a fenced block around the JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
