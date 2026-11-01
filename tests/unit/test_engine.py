"""The engine on the demo cluster with a scripted model: phases, the gate, validation, control."""

from pathlib import Path
from typing import Any

import pytest

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities.builtin import builtin_registry
from k8sholmes.engine import Engine
from k8sholmes.environments import connect
from k8sholmes.errors import InvestigationStateError, ModelFailure, PermissionRefused
from k8sholmes.store import Store
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.demo import PLANTED_INSTRUCTION, PLANTED_TOKEN, build
from k8sholmes.testing.model import calls, structured, text
from k8sholmes.thread import json_object

FRAMING = structured(
    {
        "question": "Why is api restarting?",
        "namespaces": ["shop"],
        "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
        "period_hours": 48,
        "hypotheses": ["out of memory"],
        "intended_capabilities": ["k8s.workload_health"],
    }
)


class World:
    def __init__(self, tmp_path: Path, *, after_fix: bool = False) -> None:
        self.store = Store(tmp_path / "db")
        self.auth = Auth(self.store)
        self.cluster = build(after_fix=after_fix)
        env = records.Environment(name="demo", context="demo")
        self.store.put(env)
        self.env = connect(self.store, env, self.cluster)
        self.user = self.auth.create_user(
            "inv", "a strong one", grants=[records.Grant(environment_id=self.env.id, role="investigator")]
        )
        self.viewer = self.auth.create_user(
            "view", "a strong one", grants=[records.Grant(environment_id=self.env.id, role="viewer")]
        )
        self.registry = builtin_registry(extensions=False)
        self.model = ScriptedModel()
        self.slept: list[float] = []
        self.clock_value = 0.0
        self.engine = Engine(
            self.store,
            self.auth,
            self.registry,
            cluster_for=lambda e: self.cluster,
            model_for=lambda inv: self.model,
            sleep=self.slept.append,
            clock=lambda: self.clock_value,
        )
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.engine.on_event = lambda kind, payload: self.events.append((kind, payload))

    def investigate(
        self, question: str = "Why is api restarting?", replies: list | None = None, script=None
    ) -> records.Investigation:
        self.model = ScriptedModel(replies, script=script)
        inv = self.engine.start(
            environment=self.env, question=question, user=self.user, scope=records.Scope(namespaces=["shop"])
        )
        return self.engine.run(inv.id, self.user)

    def evidence(self, inv: records.Investigation) -> list[records.Evidence]:
        return self.store.find(records.Evidence, {"investigation_id": inv.id})

    def findings(self, inv: records.Investigation) -> list[records.Finding]:
        return self.store.find(records.Finding, {"investigation_id": inv.id})


def conclusion_script(
    world: World, *, invent_evidence: bool = False, invent_number: bool = False, unused: bool = False
):
    """A model that frames, calls workload_health once, then concludes citing the real evidence."""

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        if schema and schema.get("title") == "conclusion":
            evidence = [e for e in world.store.find(records.Evidence) if e.source == "k8s.workload_health"]
            ev_id = "ev_doesnotexist" if invent_evidence else evidence[-1].id
            explanation = "The api container was OOMKilled after a 256Mi limit; the previous log ends with out of memory."
            if invent_number:
                explanation += " It restarted 9999 times."
            findings = [
                {
                    "title": "api is OOMKilled under its 256Mi limit",
                    "explanation": explanation,
                    "kind": "observation",
                    "condition": "pod.crashloop.oom",
                    "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
                    "evidence_for": [ev_id],
                    "evidence_against": [],
                    "uncertainty": "whether the cache size is the only cause",
                    "missing_information": ["memory use over time"],
                    "next_steps": ["raise the limit"],
                }
            ]
            if unused:
                findings.append(
                    {
                        "title": "legacy-export is unused",
                        "explanation": "The service has no backends.",
                        "kind": "hypothesis",
                        "condition": "service.unused",
                        "resources": [{"kind": "Service", "namespace": "shop", "name": "legacy-export"}],
                        "evidence_for": [evidence[-1].id],
                        "evidence_against": [],
                        "uncertainty": "",
                        "missing_information": [],
                        "next_steps": [],
                    }
                )
            return structured(
                {
                    "answer": "api restarts because it is OOMKilled under a 256Mi limit.",
                    "findings": findings,
                    "limitations": [],
                    "next_steps": ["raise the limit"],
                    "knowledge_used": [],
                }
            )
        if not any(m.get("role") == "tool" for m in messages):
            return calls(("k8s.workload_health", {"kind": "deployment", "name": "api", "namespace": "shop"}))
        return text("done")

    return script


def test_an_investigation_runs_through_every_phase(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.investigate(script=conclusion_script(world))
    assert inv.status == "completed"
    assert inv.framing is not None
    assert inv.framing.scope.namespaces == ["shop"]
    phases = [s.phase for s in world.store.find(records.Step, {"investigation_id": inv.id}, order_by="seq")]
    assert phases == ["framing", "discovery", "gathering", "gathering", "concluding"]
    assert all(s.status == "completed" for s in world.store.find(records.Step, {"investigation_id": inv.id}))
    findings = world.findings(inv)
    assert len(findings) == 1
    assert findings[0].condition == "pod.crashloop.oom"
    assert findings[0].kind == "observation"
    assert findings[0].author == "model:scripted"
    assert findings[0].evidence_for[0] in {e.id for e in world.evidence(inv)}
    assert inv.answer is not None
    assert inv.answer.startswith("api restarts")
    assert inv.usage.model_calls == 4
    assert {k for k, _ in world.events} >= {"status", "step", "call", "model_call", "concluded"}
    kinds = {e.source for e in world.evidence(inv)}
    assert kinds == {"k8s.inventory", "k8s.workload_health"}


def test_the_model_sees_knowledge_apart_from_evidence_and_never_the_secret(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.store.put(
        records.Knowledge(
            environment_id=world.env.id,
            kind="criticality",
            scope=records.ResourceRef(kind="Deployment", namespace="shop", name="api"),
            text="api is tier 1",
            author="user:u",
        )
    )
    inv = world.investigate(script=conclusion_script(world))
    assert inv.status == "completed"
    assert world.model.saw("user_provided_knowledge (not evidence)")
    assert world.model.saw("api is tier 1")
    assert not world.model.saw(PLANTED_TOKEN)
    assert not any(PLANTED_TOKEN in str(e.data) for e in world.evidence(inv))
    assert inv.knowledge_in_scope


def test_an_invented_evidence_id_is_repaired_once_then_limited(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.investigate(script=conclusion_script(world, invent_evidence=True))
    assert inv.status == "partial"
    assert world.findings(inv) == []
    assert any("does not exist" in lim for lim in inv.limitations)
    assert inv.usage.model_calls == 5  # one repair attempt


def test_a_number_without_evidence_is_refused(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.investigate(script=conclusion_script(world, invent_number=True))
    assert world.findings(inv) == []
    assert any("9999" in lim for lim in inv.limitations)


def test_unused_without_traffic_becomes_a_limitation(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.investigate(script=conclusion_script(world, unused=True))
    assert [f.condition for f in world.findings(inv)] == ["pod.crashloop.oom"]
    assert any("unused" in lim and "no connected source" in lim for lim in inv.limitations)
    assert inv.status == "partial"


def test_refused_and_invalid_calls_are_recorded_and_make_the_investigation_partial(tmp_path: Path) -> None:
    world = World(tmp_path)

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        if schema:
            return structured(
                {
                    "answer": "nothing",
                    "findings": [],
                    "limitations": [],
                    "next_steps": [],
                    "knowledge_used": [],
                }
            )
        if not any(m.get("role") == "tool" for m in messages):
            return calls(
                ("http.probe_url", {"url": "http://example.invalid/"}),
                ("k8s.get", {"kind": "pod", "nonsense": 1}),
                ("k8s.delete_namespace", {"namespace": "shop"}),
            )
        return text("done")

    inv = world.investigate(script=script)
    statuses = {
        c.capability_id: c.status for c in world.store.find(records.Call, {"investigation_id": inv.id})
    }
    assert statuses["http.probe_url"] == "refused"
    assert statuses["k8s.get"] == "invalid"
    assert statuses["k8s.delete_namespace"] == "invalid"
    assert inv.status == "partial"
    assert any("refused" in lim for lim in inv.limitations)
    assert "call_refused" in [a.action for a in world.store.find(records.AuditEntry)]
    tool_names = {t["function"]["name"] for t in world.model.requests[1]["tools"]}
    assert "http.probe_url" not in tool_names  # a disabled probe is not even offered


def test_an_instruction_in_a_log_changes_nothing(tmp_path: Path) -> None:
    world = World(tmp_path)

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        if schema:
            return structured(
                {
                    "answer": "nothing",
                    "findings": [],
                    "limitations": [],
                    "next_steps": [],
                    "knowledge_used": [],
                }
            )
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            return calls(
                ("k8s.logs", {"pod": "legacy-export-77aa-tt4w2", "namespace": "shop", "container": "export"})
            )
        if len(tool_messages) == 1:
            assert PLANTED_INSTRUCTION in tool_messages[0]["content"]  # the model saw the instruction as data
            return calls(("k8s.delete_namespace", {"namespace": "shop"}))
        return text("done")

    inv = world.investigate(script=script)
    call = next(
        c
        for c in world.store.find(records.Call, {"investigation_id": inv.id})
        if c.capability_id == "k8s.delete_namespace"
    )
    assert call.status == "invalid"
    assert world.cluster.calls and all(c[0] != "delete" for c in world.cluster.calls)


def test_viewer_cannot_start_and_budgets_stop_the_loop(tmp_path: Path) -> None:
    world = World(tmp_path)
    with pytest.raises(PermissionRefused, match="investigator role"):
        world.engine.start(environment=world.env, question="x", user=world.viewer)
    world.model = ScriptedModel(
        script=lambda m, t, s: (
            FRAMING
            if s and s.get("title") == "framing"
            else structured(
                {"answer": "", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}
            )
            if s
            else calls(("k8s.permissions", {}))
        )
    )
    inv = world.engine.start(
        environment=world.env, question="loop", user=world.user, budgets=records.Budgets(steps=5)
    )
    inv = world.engine.run(inv.id, world.user)
    assert inv.status == "partial"
    assert any("step budget" in lim for lim in inv.limitations)
    assert inv.current_step <= 6


def test_cancel_keeps_work_and_resume_continues(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = {"n": 0}

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        if schema:
            return structured(
                {"answer": "done", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}
            )
        state["n"] += 1
        if state["n"] == 2:
            world.engine.cancel(inv.id, world.user)  # the user cancels while the model thinks
        if state["n"] <= 3:
            return calls(("k8s.permissions", {}))
        return text("done")

    world.model = ScriptedModel(script=script)
    inv = world.engine.start(environment=world.env, question="cancel me", user=world.user)
    inv = world.engine.run(inv.id, world.user)
    assert inv.status == "cancelled"
    kept = world.store.find(records.Call, {"investigation_id": inv.id})
    assert len(kept) >= 2  # the inventory and the first permissions call stayed
    with pytest.raises(InvestigationStateError):
        world.engine.cancel(inv.id, world.user)
    resumed = world.engine.resume(inv.id, world.user)
    assert resumed.status == "completed"
    assert resumed.framing == inv.framing  # not framed again
    phases = [s.phase for s in world.store.find(records.Step, {"investigation_id": inv.id}, order_by="seq")]
    assert phases.count("framing") == 1
    assert phases.count("discovery") == 1


def test_interrupted_investigations_are_marked_and_resume(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.engine.start(environment=world.env, question="x", user=world.user)
    world.store.put(inv.model_copy(update={"status": "running"}))
    world.store.put(records.Step(investigation_id=inv.id, seq=1, phase="gathering"))
    marked = world.engine.mark_interrupted()
    assert [m.id for m in marked] == [inv.id]
    assert world.store.find(records.Step, {"investigation_id": inv.id})[0].status == "interrupted"
    world.model = ScriptedModel(script=conclusion_script(world))
    resumed = world.engine.resume(inv.id, world.user)
    assert resumed.status == "completed"


def test_follow_up_revises_rather_than_replaces(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.investigate(script=conclusion_script(world))
    first = world.findings(inv)[0]

    def follow(messages, tools, schema):
        if schema:
            evidence = [e for e in world.store.find(records.Evidence) if e.source == "k8s.workload_health"]
            return structured(
                {
                    "answer": "still OOM, now with the change confirmed",
                    "findings": [
                        {
                            "title": "api is OOMKilled under its 256Mi limit",
                            "explanation": "The limit was lowered to 256Mi in revision 3 and the container is OOMKilled.",
                            "kind": "observation",
                            "condition": "pod.crashloop.oom",
                            "resources": [{"kind": "Deployment", "namespace": "shop", "name": "api"}],
                            "evidence_for": [e.id for e in evidence],
                            "evidence_against": [],
                            "uncertainty": "",
                            "missing_information": [],
                            "next_steps": [],
                        }
                    ],
                    "limitations": [],
                    "next_steps": [],
                    "knowledge_used": [],
                }
            )
        return (
            text("I will check the changes.")
            if any(m.get("role") == "user" and "changed" in m.get("content", "") for m in messages)
            else text("done")
        )

    world.model = ScriptedModel(script=follow)
    after = world.engine.follow_up(inv.id, world.user, "What changed before this started?")
    assert after.status == "completed"
    findings = world.findings(after)
    assert len(findings) == 1
    assert findings[0].id == first.id
    assert findings[0].revisions and findings[0].revisions[0].before["explanation"].startswith(
        "The api container"
    )
    assert "revision 3" in findings[0].explanation
    rounds = {m.round for m in world.store.find(records.Message, {"investigation_id": inv.id})}
    assert rounds == {0, 1}
    assert world.model.saw("What changed before this started?")


def test_accepted_exception_applies_exactly_in_the_next_investigation(tmp_path: Path) -> None:
    world = World(tmp_path)
    sched = records.ResourceRef(kind="Deployment", namespace="shop", name="scheduler")

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(
                {
                    "question": "single replicas?",
                    "namespaces": ["shop"],
                    "resources": [],
                    "period_hours": 24,
                    "hypotheses": [],
                    "intended_capabilities": ["k8s.safeguards"],
                }
            )
        if schema:
            ev = [e for e in world.store.find(records.Evidence) if e.source == "k8s.safeguards"][-1]

            def mk(name: str) -> dict[str, Any]:
                return {
                    "title": f"{name} runs one replica",
                    "explanation": "replicas is 1",
                    "kind": "observation",
                    "condition": "replicas.single",
                    "resources": [{"kind": "Deployment", "namespace": "shop", "name": name}],
                    "evidence_for": [ev.id],
                    "evidence_against": [],
                    "uncertainty": "",
                    "missing_information": [],
                    "next_steps": [],
                }

            return structured(
                {
                    "answer": "two single-replica workloads",
                    "findings": [mk("scheduler"), mk("worker")],
                    "limitations": [],
                    "next_steps": [],
                    "knowledge_used": [],
                }
            )
        if not any(m.get("role") == "tool" for m in messages):
            return calls(("k8s.safeguards", {"namespaces": ["shop"]}))
        return text("done")

    first = world.investigate("Which workloads run a single replica?", script=script)
    assert {f.state for f in world.findings(first)} == {"open"}
    from k8sholmes import knowledge

    knowledge.create_exception(
        world.store,
        records.AcceptedException(
            environment_id=world.env.id,
            condition="replicas.single",
            scope=sched,
            explanation="singleton scheduler; downtime acceptable",
            approved_by=f"user:{world.user.id}",
        ),
    )
    second = world.investigate("Which workloads run a single replica?", script=script)
    by_name = {f.resources[0].name: f for f in world.findings(second)}
    assert by_name["scheduler"].state == "accepted_risk"
    assert by_name["scheduler"].exception_id is not None
    assert by_name["worker"].state == "open"  # a similar finding on another resource is not covered
    assert world.model.saw("accepted_exceptions (not evidence)")


def test_model_failures_retry_only_when_retryable(tmp_path: Path) -> None:
    world = World(tmp_path)
    attempts = {"n": 0}

    def flaky(messages, tools, schema):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelFailure("HTTP 503", retryable=True, status=503)
        if attempts["n"] == 2:
            raise ModelFailure("HTTP 400 bad request", retryable=False, status=400)
        return FRAMING

    inv = world.investigate(script=flaky)
    assert inv.status == "failed"
    assert any("the model failed" in lim for lim in inv.limitations)
    calls_made = world.store.find(records.ModelCall, {"investigation_id": inv.id})
    assert [c.status for c in calls_made] == ["retried", "failed"]
    assert len({(c.endpoint_id, c.model) for c in calls_made}) == 1  # retried on the same endpoint
    assert world.slept == [1.0]


def test_json_object_is_tolerant() -> None:
    assert json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert json_object('Sure! {"a": 1} there') == {"a": 1}
    assert json_object("nothing") == {}
    assert json_object("[1, 2]") == {}


def test_token_call_and_time_budgets(tmp_path: Path) -> None:
    world = World(tmp_path)
    empty = structured(
        {"answer": "", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}
    )

    def two_calls(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        if schema:
            return empty
        return calls(("k8s.permissions", {}), ("k8s.top", {"kind": "pods"}))

    world.model = ScriptedModel(script=two_calls)
    inv = world.engine.start(
        environment=world.env,
        question="budgets",
        user=world.user,
        budgets=records.Budgets(calls_per_step=1, tokens=350),
    )
    inv = world.engine.run(inv.id, world.user)
    assert any("token budget" in lim for lim in inv.limitations)
    tool_messages = [
        m for m in world.store.find(records.Message, {"investigation_id": inv.id}) if m.role == "tool"
    ]
    assert any("call budget was spent" in m.content for m in tool_messages)
    made = {c.capability_id for c in world.store.find(records.Call, {"investigation_id": inv.id})}
    assert "k8s.top" not in made  # the second call of the step was never run
    world.model = ScriptedModel(script=two_calls)
    inv = world.engine.start(
        environment=world.env, question="time", user=world.user, budgets=records.Budgets(wall_ms=1)
    )

    def ticking(messages, tools, schema):
        world.clock_value += 1.0
        return two_calls(messages, tools, schema)

    world.model = ScriptedModel(script=ticking)
    inv = world.engine.run(inv.id, world.user)
    assert any("time budget" in lim for lim in inv.limitations)


def test_unexpected_errors_leave_running_and_a_dangling_tool_call_gets_a_reply(tmp_path: Path) -> None:
    world = World(tmp_path)

    class BrokenError(Exception):
        pass

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return FRAMING
        raise BrokenError("the store exploded")

    world.model = ScriptedModel(script=script)
    inv = world.engine.start(environment=world.env, question="boom", user=world.user)
    with pytest.raises(BrokenError):
        world.engine.run(inv.id, world.user)
    inv = world.store.get(records.Investigation, inv.id)
    assert inv.status == "failed"
    assert any("BrokenError" in lim for lim in inv.limitations)
    assert all(s.status != "running" for s in world.store.find(records.Step, {"investigation_id": inv.id}))
    # a tool call the process never answered gets a synthesized reply when the thread is replayed
    from k8sholmes import thread

    world.engine._append(
        inv,
        "assistant",
        "",
        author="model:x",
        tool_calls=[records.ToolCall(id="call_9", name="k8s.top", arguments={})],
    )
    replayed = thread.messages_for(world.store, inv, world.env, [], [])
    assert any(m.get("tool_call_id") == "call_9" and "interrupted" in m["content"] for m in replayed)


def test_a_non_json_framing_is_a_limitation_and_a_non_json_conclusion_is_partial(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.model = ScriptedModel(script=lambda m, t, s: text("I would rather chat.") if s else text("done"))
    inv = world.engine.start(environment=world.env, question="prose", user=world.user)
    inv = world.engine.run(inv.id, world.user)
    assert inv.status == "partial"
    assert any("framing was not the JSON" in lim for lim in inv.limitations)
    assert any("not the JSON object" in lim for lim in inv.limitations)


def test_a_follow_up_during_a_run_is_a_redirect(tmp_path: Path) -> None:
    world = World(tmp_path)
    inv = world.engine.start(environment=world.env, question="x", user=world.user)
    world.store.put(inv.model_copy(update={"status": "running"}))
    same = world.engine.follow_up(inv.id, world.user, "look at the worker too")
    assert same.status == "running"
    messages = world.store.find(records.Message, {"investigation_id": inv.id})
    assert messages[-1].content == "look at the worker too" and messages[-1].round == 0
    with pytest.raises(InvestigationStateError):
        world.engine.resume(inv.id, world.user)  # running is not resumable
