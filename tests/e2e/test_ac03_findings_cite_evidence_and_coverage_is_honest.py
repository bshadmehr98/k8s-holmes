"""Guarantees 3 and 4: findings cite evidence that exists; coverage turns unsupported claims into limitations."""

from k8sholmes.records import Evidence, Finding
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


def finding(
    title: str, condition: str, evidence: list[str], explanation: str = "The evidence shows it."
) -> dict:
    return {
        "title": title,
        "explanation": explanation,
        "kind": "observation",
        "condition": condition,
        "resources": [{"kind": "Service", "namespace": "shop", "name": "legacy-export"}],
        "evidence_for": evidence,
        "evidence_against": [],
        "uncertainty": "",
        "missing_information": [],
        "next_steps": [],
    }


def model(demo: Demo, conclude):
    def script(messages, tools, schema):
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            return structured(conclude())
        if not any(m.get("role") == "tool" for m in messages):
            return calls(("k8s.service_backends", {"name": "legacy-export", "namespace": "shop"}))
        return text("done")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=script)


def test_an_evidence_id_that_does_not_exist_is_repaired_then_dropped(demo: Demo) -> None:
    attempts = {"n": 0}

    def conclude():
        attempts["n"] += 1
        return {
            "answer": "x",
            "findings": [finding("ghost", "custom.ghost", ["ev_nope"])],
            "limitations": [],
            "next_steps": [],
            "knowledge_used": [],
        }

    model(demo, conclude)
    inv = demo.ask("cite nothing")
    assert attempts["n"] == 2  # one repair
    assert demo.app.store.find(Finding, {"investigation_id": inv.id}) == []
    assert inv.status == "partial"
    assert any("ev_nope" in lim for lim in inv.limitations)


def test_every_stored_finding_cites_evidence_that_exists(demo: Demo) -> None:
    def conclude():
        backends = [e for e in demo.app.store.find(Evidence) if e.source == "k8s.service_backends"][-1]
        return {
            "answer": "x",
            "findings": [finding("legacy-export has no backends", "service.no-backends", [backends.id])],
            "limitations": [],
            "next_steps": [],
            "knowledge_used": [],
        }

    model(demo, conclude)
    inv = demo.ask("backends?")
    findings = demo.app.store.find(Finding, {"investigation_id": inv.id})
    assert len(findings) == 1
    evidence_ids = {e.id for e in demo.app.store.find(Evidence, {"investigation_id": inv.id})}
    assert set(findings[0].evidence_for) <= evidence_ids
    assert inv.status == "completed"


def test_unused_isolated_and_traced_need_a_source(demo: Demo) -> None:
    def conclude():
        backends = [e for e in demo.app.store.find(Evidence) if e.source == "k8s.service_backends"][-1]
        return {
            "answer": "x",
            "findings": [
                finding("legacy-export is unused", "service.unused", [backends.id], "Nobody calls it."),
                finding("shop is isolated from external networks", "custom.isolated", [backends.id]),
                finding("the trace shows web calling api", "custom.path", [backends.id]),
                finding("legacy-export has no backends", "service.no-backends", [backends.id]),
            ],
            "limitations": [],
            "next_steps": [],
            "knowledge_used": [],
        }

    model(demo, conclude)
    inv = demo.ask("unused?")
    stored = demo.app.store.find(Finding, {"investigation_id": inv.id})
    assert [f.condition for f in stored] == ["service.no-backends"]
    texts = " ".join(inv.limitations)
    assert "unused" in texts and "isolated from" in texts and "the trace shows" in texts
    assert inv.status == "partial"


def test_a_finding_without_supporting_evidence_is_never_stored(demo: Demo) -> None:
    def conclude():
        return {
            "answer": "x",
            "findings": [finding("baseless", "custom.baseless", [])],
            "limitations": [],
            "next_steps": [],
            "knowledge_used": [],
        }

    model(demo, conclude)
    inv = demo.ask("prove nothing")
    assert demo.app.store.find(Finding, {"investigation_id": inv.id}) == []
    assert inv.status == "partial"
    assert any("no supporting evidence" in lim for lim in inv.limitations)
