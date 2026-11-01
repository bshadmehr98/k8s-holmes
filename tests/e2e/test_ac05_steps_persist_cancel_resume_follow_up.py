"""Guarantees 5, 6 and 7: steps persist across an interruption, cancel keeps work, follow-up continues."""

import os
import subprocess
import sys
from pathlib import Path

from k8sholmes.records import Call, Evidence, Finding, Investigation, Message, Step
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.model import calls, structured, text
from tests.e2e.conftest import Demo

FRAME = {
    "question": "q",
    "namespaces": ["shop"],
    "resources": [],
    "period_hours": 24,
    "hypotheses": [],
    "intended_capabilities": [],
}
EMPTY = {"answer": "nothing", "findings": [], "limitations": [], "next_steps": [], "knowledge_used": []}

KILLED = """
import os, sys
from k8sholmes.app import App
from k8sholmes.records import Investigation
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.model import calls, structured, text

app = App.open(sys.argv[1], extensions=False)
env = app.environment("demo")
user = app.store.find(__import__("k8sholmes.records", fromlist=["User"]).User, {"username": "demo"})[0]
state = {"n": 0}

def script(messages, tools, schema):
    if schema and schema.get("title") == "framing":
        return structured({"question": "q", "namespaces": ["shop"], "resources": [], "period_hours": 24, "hypotheses": [], "intended_capabilities": []})
    state["n"] += 1
    if state["n"] == 3:
        os._exit(9)  # the process dies while the third gathering step runs
    return calls(("k8s.permissions", {}))

app.engine.model_for = lambda inv: ScriptedModel(script=script)
inv = app.engine.start(environment=env, question="die mid-step", user=user)
print(inv.id)
sys.stdout.flush()
app.engine.run(inv.id, user)
"""


def test_a_killed_process_leaves_completed_steps_and_the_investigation_resumes(demo: Demo) -> None:
    done = subprocess.run(
        [sys.executable, "-c", KILLED, str(demo.app.home)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        timeout=60,
    )
    assert done.returncode == 9, done.stderr
    inv_id = done.stdout.strip().splitlines()[-1]
    app = demo.app
    inv = app.store.get(Investigation, inv_id)
    assert inv.status == "running"  # the process died before anything could say otherwise
    completed = [s for s in app.store.find(Step, {"investigation_id": inv_id}) if s.status == "completed"]
    assert [s.phase for s in completed][:4] == ["framing", "discovery", "gathering", "gathering"]
    assert len(app.store.find(Call, {"investigation_id": inv_id})) >= 3
    assert len(app.store.find(Evidence, {"investigation_id": inv_id})) >= 3
    marked = app.engine.mark_interrupted()
    assert [m.id for m in marked] == [inv_id]
    assert any(s.status == "interrupted" for s in app.store.find(Step, {"investigation_id": inv_id}))

    def finish(messages, tools, schema):
        return structured(EMPTY) if schema else text("done")

    app.engine.model_for = lambda inv: ScriptedModel(script=finish)
    resumed = app.engine.resume(inv_id, demo.user)
    assert resumed.status == "completed"
    phases = [s.phase for s in app.store.find(Step, {"investigation_id": inv_id}, order_by="seq")]
    assert phases.count("framing") == 1 and phases.count("discovery") == 1
    assert len(app.store.find(Evidence, {"investigation_id": inv_id})) >= 3  # nothing was lost


def test_cancel_keeps_work_and_resume_continues(demo: Demo) -> None:
    state = {"n": 0, "inv": None}

    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            return structured(EMPTY)
        state["n"] += 1
        if state["n"] == 2:
            demo.app.engine.cancel(state["inv"].id, demo.user)
        return calls(("k8s.permissions", {})) if state["n"] <= 2 else text("done")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=script)
    inv = demo.app.engine.start(environment=demo.env, question="cancel", user=demo.user)
    state["inv"] = inv
    inv = demo.app.engine.run(inv.id, demo.user)
    assert inv.status == "cancelled"
    calls_before = len(demo.app.store.find(Call, {"investigation_id": inv.id}))
    assert calls_before >= 2
    resumed = demo.app.engine.resume(inv.id, demo.user)
    assert resumed.status == "completed"
    assert len(demo.app.store.find(Call, {"investigation_id": inv.id})) >= calls_before


def test_follow_up_sees_the_thread_and_revises(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    first = {f.condition: f for f in demo.app.store.find(Finding, {"investigation_id": inv.id})}
    oom = first["pod.crashloop.oom"]
    seen: list[str] = []

    def follow(messages, tools, schema):
        import json

        seen.append(json.dumps(messages))
        if schema:
            evidence = [
                e
                for e in demo.app.store.find(Evidence, {"investigation_id": inv.id})
                if e.source in ("k8s.workload_health", "k8s.changes")
            ]
            return structured(
                {
                    "answer": "the restarts follow the revision that lowered the limit",
                    "findings": [
                        {
                            "title": oom.title,
                            "explanation": "The limit was lowered to 256Mi in revision 3 and the container is OOMKilled since.",
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
        return text("I will look at the changes again.")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=follow)
    after = demo.app.engine.follow_up(inv.id, demo.user, "What changed before this started?")
    assert after.status == "completed", after.limitations
    messages = demo.app.store.find(Message, {"investigation_id": inv.id}, order_by="seq")
    assert messages[0].content == "Why is api restarting?"
    assert any(
        m.role == "user" and m.content == "What changed before this started?" and m.round == 1
        for m in messages
    )
    assert all(oom.evidence_for[0] in s for s in seen), "the follow-up saw the earlier evidence"
    later = {f.condition: f for f in demo.app.store.find(Finding, {"investigation_id": inv.id})}
    revised = later["pod.crashloop.oom"]
    assert revised.id == oom.id
    assert revised.revisions, "revised, not replaced"
    assert revised.revisions[0].before["explanation"] == oom.explanation
    assert revised.revisions[0].author == "model:scripted"
    assert "revision 3" in revised.explanation
