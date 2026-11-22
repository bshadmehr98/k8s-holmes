"""The investigation engine (design §6): framing, discovery, gathering, concluding, follow-up.

Every step is committed as it completes (§16.2). The registry's gate decides by code whether each
capability the model asks for may run; the conclusion is validated against the evidence (§6.3). What the
model sees is built in `thread.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from k8sholmes import actions, knowledge, thread
from k8sholmes import findings as findings_mod
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Capability, Context, Registry
from k8sholmes.capabilities.inputs import known_for
from k8sholmes.cluster import Cluster
from k8sholmes.environments import connect, latest_inventory
from k8sholmes.errors import InvestigationStateError, ModelFailure
from k8sholmes.models import Model, Reply, tool_definition
from k8sholmes.playbooks import PLAYBOOKS, plan_for
from k8sholmes.records import (
    AcceptedException,
    Budgets,
    Call,
    Environment,
    Evidence,
    Finding,
    Framing,
    Investigation,
    Message,
    ModelCall,
    Period,
    Phase,
    Scope,
    Step,
    ToolCall,
    User,
    new_id,
    now,
)
from k8sholmes.store import Store

RETRY_BACKOFF_S = (1.0, 3.0)
RESUMABLE = ("cancelled", "interrupted", "partial", "failed")
EventHook = Callable[[str, dict[str, Any]], None]


class Engine:
    """Runs investigations against a store, a registry, a cluster per environment and a model per endpoint."""

    def __init__(
        self,
        store: Store,
        auth: Auth,
        registry: Registry,
        *,
        cluster_for: Callable[[Environment], Cluster],
        model_for: Callable[[str | None], Model],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.auth = auth
        self.registry = registry
        self.cluster_for = cluster_for
        self.model_for = model_for
        self.sleep = sleep
        self.clock = clock
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
        inv = Investigation(
            environment_id=environment.id,
            question=question,
            scope=scope or Scope(),
            period=period or Period(),
            created_by=user.id,
            model_endpoint_id=model_endpoint_id or environment.settings.default_model_id,
            budgets=budgets or Budgets(),
            saved_id=saved_id,
        )
        self.store.put(inv)
        self.store.put(
            Message(investigation_id=inv.id, seq=0, role="user", content=question, author=f"user:{user.id}")
        )
        self.auth.audit(
            user.id, "investigation_started", environment_id=environment.id, subject=f"investigation:{inv.id}"
        )
        return inv

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
        if inv.status not in RESUMABLE:
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

    def change_model(self, investigation_id: str, user: User, endpoint_id: str) -> Investigation:
        """A user's choice of another endpoint for the rest of an investigation, recorded in the thread and
        the audit log (design §13.3); the caller checks the endpoint is usable."""
        inv = self.store.get(Investigation, investigation_id)
        self.auth.require(user, inv.environment_id, "investigator")
        if endpoint_id == inv.model_endpoint_id:
            return inv
        note = f"{user.username} changed the model endpoint from {inv.model_endpoint_id} to {endpoint_id}."
        self._append(inv, "user", note, author=f"user:{user.id}", round_=self._round(inv))
        self.auth.audit(
            user.id,
            "investigation_model_changed",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
            details={"from": inv.model_endpoint_id, "to": endpoint_id},
        )
        return self._update(inv, model_endpoint_id=endpoint_id)

    def follow_up(self, investigation_id: str, user: User, message: str) -> Investigation:
        """A user's message, context or requested check: another round of gathering and concluding (§6.1)."""
        inv = self.store.get(Investigation, investigation_id)
        self.auth.require(user, inv.environment_id, "investigator")
        if inv.status in ("queued", "running"):
            self._append(
                inv, "user", message, author=f"user:{user.id}", round_=self._round(inv)
            )  # a redirect
            return inv
        self._append(inv, "user", message, author=f"user:{user.id}", round_=self._round(inv) + 1)
        self.auth.audit(
            user.id,
            "investigation_follow_up",
            environment_id=inv.environment_id,
            subject=f"investigation:{inv.id}",
        )
        stale = [n.model_copy(update={"superseded": True}) for n in inv.notices]
        inv = self._update(inv, status="running", notices=stale)
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
        if inv.status not in ("queued", *RESUMABLE):
            raise InvestigationStateError(f"investigation {inv.id} is {inv.status}")
        stale = [n.model_copy(update={"superseded": True}) for n in inv.notices]  # a new run, a new count
        inv = self._update(inv, status="running", notices=stale)
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
        started = self.clock()
        try:
            model = self.model_for(inv.model_endpoint_id)
            environment = self.store.get(Environment, inv.environment_id)
            cluster = self.cluster_for(environment)
            if environment.connected_at is None:
                environment = connect(
                    self.store, environment, cluster
                )  # the matrix decides availability (§4.1)
            for phase in phases:
                inv = self.store.get(Investigation, inv.id)
                if inv.status == "cancelled":
                    return inv
                if phase == "framing":
                    inv = self._framing(inv, model, [c.id for c in self._capabilities(environment, user)[0]])
                elif phase == "discovery":
                    inv = self._discovery(inv, environment, cluster, user)
                elif phase == "gathering":
                    inv = self._gathering(inv, model, environment, cluster, user, started)
                elif phase == "concluding":
                    inv = self._concluding(inv, model, environment, user, follow_up=follow_up)
        except ModelFailure as exc:
            inv = self._fail(inv, f"the model failed: {exc}")
        except Exception as exc:
            inv = self._fail(inv, f"{type(exc).__name__}: {exc}")
            raise
        return inv

    def _fail(self, inv: Investigation, reason: str) -> Investigation:
        """The investigation leaves `running` whatever went wrong (design §6.5, §16.2)."""
        for step in self.store.find(Step, {"investigation_id": inv.id, "status": "running"}):
            self.store.put(
                step.model_copy(update={"status": "failed", "finished_at": now(), "summary": reason[:500]})
            )
        fresh = self.store.get(Investigation, inv.id)
        failed = self._update(fresh, status="failed", limitations=[*fresh.limitations, reason])
        self._emit("failed", {"investigation_id": inv.id, "error": reason})
        return failed

    # phases

    def _framing(self, inv: Investigation, model: Model, capability_ids: list[str]) -> Investigation:
        step = self._begin_step(inv, "framing")
        schema = thread.framing_schema(capability_ids)
        reply, inv = self._call_model(inv, model, "framing", thread.framing_prompt(inv), schema=schema)
        payload = thread.json_object(reply.content)
        limitations = (
            inv.limitations
            if payload
            else [
                *inv.limitations,
                "the model's framing was not the JSON object asked for; the question was taken as given",
            ]
        )
        framing = thread.framing_from(payload, inv)
        framing = framing.model_copy(
            update={
                "intended_capabilities": [c for c in framing.intended_capabilities if c in capability_ids]
            }
        )
        inv = self._update(inv, framing=framing, limitations=limitations)
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
        available, _ = self._capabilities(environment, user)
        self._end_step(
            inv,
            step,
            f"{len(available)} capabilities available; {len(notes)} notes and {len(exceptions)} exceptions",
        )
        return inv

    def _playbook(
        self, inv: Investigation, model: Model, environment: Environment, cluster: Cluster, user: User
    ) -> Investigation:
        """The playbook the framing named, run once through the gate before the model gathers (0002)."""
        framing = inv.framing
        if framing is None or framing.playbook not in PLAYBOOKS or inv.playbook:
            return inv
        book = PLAYBOOKS[framing.playbook]
        plan, limitations = plan_for(book, framing, latest_inventory(self.store, environment.id))
        if book.needs and not any(book.needs in c.systems for c in self._capabilities(environment, user)[0]):
            limitations.append(book.limitation)
        structured = getattr(model, "tool_mode", "native") == "structured"
        tool_calls = [ToolCall(id=new_id("pb"), name=cid, arguments=args) for cid, args in plan]
        step = self._begin_step(inv, "gathering")
        round_ = self._round(inv)
        note = f"Playbook {book.id} ({book.title}): {len(plan)} checks, run by K8sHolmes, not the model."
        self._append(
            inv,
            "assistant",
            note,
            author=f"playbook:{book.id}",
            tool_calls=[] if structured else tool_calls,
            round_=round_,
        )
        ctx = self._context(inv, environment, cluster, user)
        summaries, _ = self._run_tool_calls(
            inv, step, tool_calls, ctx, round_, structured=structured, playbook=book.id
        )
        self._end_step(inv, step, f"playbook {book.id}: " + "; ".join(summaries))
        fresh = self.store.get(Investigation, inv.id)
        return self._update(fresh, playbook=book.id, limitations=[*fresh.limitations, *limitations])

    def _gathering(
        self,
        inv: Investigation,
        model: Model,
        environment: Environment,
        cluster: Cluster,
        user: User,
        started: float,
    ) -> Investigation:
        inv = self._playbook(inv, model, environment, cluster, user)  # once, when the framing named one
        available, unavailable = self._capabilities(environment, user)
        framing = inv.framing or Framing(question=inv.question)
        available = actions.ordered(available, framing.intended_capabilities)  # 0002, part 4
        ctx = self._context(inv, environment, cluster, user)
        structured = getattr(model, "tool_mode", "native") == "structured"
        turn = self._structured_turn if structured else self._native_turn
        streak: list[Call] = []  # the latest consecutive invalid calls
        while True:
            inv = self.store.get(Investigation, inv.id)
            if inv.status == "cancelled":
                return inv
            limitation = self._budget_hit(inv, started)
            if limitation:
                return self._update(inv, limitations=[*inv.limitations, limitation])
            step = self._begin_step(inv, "gathering")
            messages = thread.messages_for(self.store, inv, environment, available, unavailable)
            content, tool_calls, inv = turn(inv, model, messages, available, ctx)
            round_ = self._round(inv)
            self._append(
                inv,
                "assistant",
                content,
                author=f"model:{model.name}",
                tool_calls=[] if structured else tool_calls,
                round_=round_,
            )
            if not tool_calls:
                self._end_step(inv, step, "the model has what it needs")
                return self.store.get(Investigation, inv.id)
            summaries, calls = self._run_tool_calls(inv, step, tool_calls, ctx, round_, structured=structured)
            self._end_step(inv, step, "; ".join(summaries))
            for call in calls:
                streak = [*streak, call] if call.status == "invalid" else []
            if len(streak) >= thread.STUCK_AFTER:
                return self._stop_invalid(inv, streak[-thread.STUCK_AFTER :])

    def _native_turn(
        self,
        inv: Investigation,
        model: Model,
        messages: list[dict[str, Any]],
        available: list[Capability],
        ctx: Context,
    ) -> tuple[str, list[ToolCall], Investigation]:
        """The model sees every available capability as a tool and writes tool calls."""
        allow = self.registry.settings_for
        tools = [
            tool_definition(
                c.id, f"{c.title}. {c.purpose}", c.schema(ctx.known, allow(c, ctx.environment).namespaces)
            )
            for c in available
        ]
        reply, inv = self._call_model(inv, model, "gathering", messages, tools=tools)
        return reply.content, reply.tool_calls, inv

    def _structured_turn(
        self,
        inv: Investigation,
        model: Model,
        messages: list[dict[str, Any]],
        available: list[Capability],
        ctx: Context,
    ) -> tuple[str, list[ToolCall], Investigation]:
        """Two small JSON decisions, choose and fill (0002, parts 1 and 4); usage is recorded per call."""

        def ask(msgs: list[dict[str, Any]], schema: dict[str, Any], step: str) -> str:
            nonlocal inv
            reply, inv = self._call_model(inv, model, "gathering", msgs, schema=schema, step=step)
            return reply.content

        def allow(cap: Capability) -> list[str]:
            return self.registry.settings_for(cap, ctx.environment).namespaces

        call, content = actions.structured_turn(ask, messages, available, ctx.known, allow)
        return content, [call] if call else [], inv

    def _stop_invalid(self, inv: Investigation, last: list[Call]) -> Investigation:
        """The model sent invalid inputs again and again: stop gathering and say so plainly (0002, part 2)."""
        notice, limitation = thread.stopped_notice(last)
        fresh = self.store.get(Investigation, inv.id)
        updated = self._update(
            fresh, notices=[*fresh.notices, notice], limitations=[*fresh.limitations, limitation]
        )
        self._emit("stopped_invalid", {"investigation_id": inv.id, "notice": notice.model_dump(mode="json")})
        return updated

    def _run_tool_calls(
        self,
        inv: Investigation,
        step: Step,
        tool_calls: list[ToolCall],
        ctx: Context,
        round_: int,
        *,
        structured: bool = False,
        playbook: str | None = None,
    ) -> tuple[list[str], list[Call]]:
        """Run each requested capability through the gate; record the call, the evidence, the result
        message (a tool message, or in the structured mode a context message marked as data)."""
        summaries: list[str] = []
        calls: list[Call] = []
        limit = len(tool_calls) if playbook else inv.budgets.calls_per_step  # a plan is capped already
        for tool_call in tool_calls[:limit]:
            call, evidence = self.registry.execute(
                tool_call.name, tool_call.arguments, ctx, self.auth, step_id=step.id
            )
            call = call.model_copy(update={"playbook": playbook})
            with self.store.transaction():
                self.store.put(call)
                if evidence is not None:
                    self.store.put(evidence)
                if structured:  # no tool call to answer: a context message marked as data
                    role, content, answers = "user", thread.result_message(call, evidence), None
                else:
                    role, content, answers = "tool", thread.tool_message(call, evidence), tool_call.id
                self._append(
                    inv,
                    role,
                    content,
                    author=f"capability:{tool_call.name}",
                    tool_call_id=answers,
                    round_=round_,
                )
            calls.append(call)
            detail = f" ({evidence.coverage})" if evidence else f" ({call.reason[:80]})"
            summaries.append(f"{tool_call.name}: {call.status}{detail}")
            if call.status == "refused":
                fresh = self.store.get(Investigation, inv.id)
                self._update(fresh, limitations=[*fresh.limitations, f"refused: {call.reason}"])
            self._emit("call", {"investigation_id": inv.id, "call": call.model_dump(mode="json")})
        for skipped in tool_calls[limit:]:
            self._append(
                inv,
                "tool",
                '{"error": "not run: the step\'s call budget was spent"}',
                author="engine",
                tool_call_id=skipped.id,
                round_=round_,
            )
            summaries.append(f"{skipped.name}: not run (call budget)")
        return summaries, calls

    def _concluding(
        self, inv: Investigation, model: Model, environment: Environment, user: User, *, follow_up: bool
    ) -> Investigation:
        step = self._begin_step(inv, "concluding")
        evidence = {e.id: e for e in self.store.find(Evidence, {"investigation_id": inv.id})}
        knowledge_ids = set(inv.knowledge_in_scope) | set(inv.exceptions_in_scope)
        available, unavailable = self._capabilities(environment, user)
        messages = [
            *thread.messages_for(self.store, inv, environment, available, unavailable),
            {"role": "user", "content": thread.CONCLUDE_INSTRUCTION},
        ]
        validated = findings_mod.Validated()
        for attempt in range(2):
            reply, inv = self._call_model(inv, model, "concluding", messages, schema=thread.CONCLUSION_SCHEMA)
            validated = findings_mod.validate_conclusion(
                thread.json_object(reply.content), evidence, knowledge_ids
            )
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
        exceptions = self.store.get_many(AcceptedException, inv.exceptions_in_scope)
        existing = self.store.find(Finding, {"investigation_id": inv.id}) if follow_up else []
        stored = findings_mod.store_findings(
            self.store, inv, validated, author=f"model:{model.name}", exceptions=exceptions, existing=existing
        )
        limitations = [*inv.limitations, *validated.limitations]
        if validated.problems:
            limitations.append(
                "the model's conclusion still had problems after one repair: " + "; ".join(validated.problems)
            )
        refused = any(c.status == "refused" for c in self.store.find(Call, {"investigation_id": inv.id}))
        status = "partial" if limitations or refused else "completed"
        inv = self._update(inv, status=status, answer=validated.answer, limitations=limitations)
        self._end_step(inv, step, f"{len(stored)} findings, {len(validated.limitations)} limitations")
        self._emit("concluded", {"investigation_id": inv.id, "status": status})
        return inv

    def conclude_only(
        self, inv: Investigation, model: Model, user: User
    ) -> tuple[findings_mod.Validated, Reply]:
        """The concluding phase alone, on the investigation's stored evidence, storing no findings (§13.4).

        A fixed-evidence model comparison calls this once per endpoint; the model call is recorded with
        the phase `comparison`, so usage is counted and the investigation's own thread stays untouched.
        """
        environment = self.store.get(Environment, inv.environment_id)
        self.auth.require(user, environment.id, "investigator")
        evidence = {e.id: e for e in self.store.find(Evidence, {"investigation_id": inv.id})}
        knowledge_ids = set(inv.knowledge_in_scope) | set(inv.exceptions_in_scope)
        available, unavailable = self._capabilities(environment, user)
        messages = [
            *thread.messages_for(self.store, inv, environment, available, unavailable),
            {"role": "user", "content": thread.CONCLUDE_INSTRUCTION},
        ]
        started = self.clock()
        try:  # one call, no retries, and the investigation's own usage and model name stay untouched
            limit = getattr(model, "context_tokens", None)
            fitted = thread.fit(messages, limit, thread.tokens_of(thread.CONCLUSION_SCHEMA))
            reply = model.complete(fitted, schema=thread.CONCLUSION_SCHEMA)
        except ModelFailure as exc:
            duration_ms = int((self.clock() - started) * 1000)
            self._record_model_call(
                inv, model, "comparison", duration_ms=duration_ms, status="failed", error=str(exc)
            )
            raise
        self._record_model_call(inv, model, "comparison", duration_ms=reply.duration_ms, reply=reply)
        validated = findings_mod.validate_conclusion(
            thread.json_object(reply.content), evidence, knowledge_ids
        )
        return validated, reply

    # capabilities and the model

    def _capabilities(
        self, environment: Environment, user: User
    ) -> tuple[list[Capability], list[tuple[str, str]]]:
        """The capabilities this user may run here, and the rest with the gate's reason."""
        available: list[Capability] = []
        unavailable: list[tuple[str, str]] = []
        for cap in self.registry.all():
            refusal = self.registry.refusal(cap, user, environment, self.auth)
            if refusal is None:
                available.append(cap)
            else:
                unavailable.append((cap.id, refusal.reason))
        return available, unavailable

    def _context(self, inv: Investigation, environment: Environment, cluster: Cluster, user: User) -> Context:
        framing = inv.framing or Framing(question=inv.question)
        return Context(
            environment=environment,
            cluster=cluster,
            store=self.store,
            user=user,
            investigation_id=inv.id,
            period=framing.period,
            known=known_for(self.store, environment, cluster),
        )

    def _call_model(
        self,
        inv: Investigation,
        model: Model,
        phase: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        step: str | None = None,
    ) -> tuple[Reply, Investigation]:
        """One model call with retries for retryable failures only, on the same endpoint; usage recorded.
        With the model's context limit, older results are compacted to fit (0002, part 7)."""
        mode = getattr(model, "tool_mode", "native")
        limit = getattr(model, "context_tokens", None)
        reserve = thread.tokens_of(tools or []) + thread.tokens_of(schema or {})
        messages = thread.fit(messages, limit, reserve)
        if limit and thread.tokens_of(messages) + reserve > limit:
            inv = self._note_over_limit(inv, limit, thread.tokens_of(messages) + reserve)
        for attempt in range(3):
            started = self.clock()
            try:
                reply = model.complete(messages, tools=tools, schema=schema)
            except ModelFailure as exc:
                retry = exc.retryable and attempt < 2
                self._record_model_call(
                    inv,
                    model,
                    phase,
                    duration_ms=int((self.clock() - started) * 1000),
                    status="retried" if retry else "failed",
                    error=str(exc),
                    mode=mode,
                    step=step,
                )
                if not retry:
                    raise
                self.sleep(RETRY_BACKOFF_S[attempt])
                continue
            self._record_model_call(
                inv, model, phase, duration_ms=reply.duration_ms, reply=reply, mode=mode, step=step
            )
            usage = inv.usage.model_copy(
                update={
                    "model_calls": inv.usage.model_calls + 1,
                    "prompt_tokens": inv.usage.prompt_tokens + reply.prompt_tokens,
                    "completion_tokens": inv.usage.completion_tokens + reply.completion_tokens,
                    "duration_ms": inv.usage.duration_ms + reply.duration_ms,
                }
            )
            inv = self._update(inv, usage=usage, model_name=model.name, tool_mode=mode)
            self._emit(
                "model_call",
                {
                    "investigation_id": inv.id,
                    "phase": phase,
                    "tokens": reply.prompt_tokens + reply.completion_tokens,
                },
            )
            return reply, inv
        raise ModelFailure(
            "the model failed", retryable=False
        )  # pragma: no cover - the loop raises or returns

    def _note_over_limit(self, inv: Investigation, limit: int, tokens: int) -> Investigation:
        """Said once: even with every older result compacted, a request is larger than the limit."""
        start = f"the context limit ({limit} tokens) is smaller than a request"
        fresh = self.store.get(Investigation, inv.id)
        if any(lim.startswith(start) for lim in fresh.limitations):
            return fresh
        text = f"{start} even with older results compacted (about {tokens} tokens); the model was sent more"
        return self._update(fresh, limitations=[*fresh.limitations, text])

    def _record_model_call(
        self,
        inv: Investigation,
        model: Model,
        phase: str,
        *,
        duration_ms: int,
        reply: Reply | None = None,
        status: str = "ok",
        error: str | None = None,
        mode: str | None = None,
        step: str | None = None,
    ) -> None:
        self.store.put(
            ModelCall(
                investigation_id=inv.id,
                endpoint_id=inv.model_endpoint_id or "",
                model=model.name,
                phase=phase,
                prompt_tokens=reply.prompt_tokens if reply else 0,
                completion_tokens=reply.completion_tokens if reply else 0,
                duration_ms=duration_ms,
                status=cast(Any, status),
                error=error,
                mode=mode,
                step=step,
            )
        )

    # bookkeeping

    def _budget_hit(self, inv: Investigation, started: float) -> str | None:
        if inv.current_step >= inv.budgets.steps:
            return f"the step budget ({inv.budgets.steps}) was spent before the model concluded"
        if inv.usage.prompt_tokens + inv.usage.completion_tokens >= inv.budgets.tokens:
            return f"the token budget ({inv.budgets.tokens}) was spent"
        if (self.clock() - started) * 1000 >= inv.budgets.wall_ms:
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
        done = step.model_copy(update={"status": "completed", "finished_at": now(), "summary": summary[:500]})
        self.store.put(done)
        self._emit("step", {"investigation_id": inv.id, "step": done.model_dump(mode="json")})

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
        return max((m.round for m in self.store.find(Message, {"investigation_id": inv.id})), default=0)

    def _update(self, inv: Investigation, **fields: Any) -> Investigation:
        fresh = self.store.get(Investigation, inv.id)
        updated = fresh.model_copy(update={**fields, "updated_at": now()})
        self.store.put(updated)
        if "status" in fields:
            self._emit("status", {"investigation_id": inv.id, "status": updated.status})
        return updated

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(kind, payload)
