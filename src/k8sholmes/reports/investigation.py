"""The live summary and the investigation report (design §10), and the plain export the command line and
the API share."""

from __future__ import annotations

from typing import Any

from k8sholmes.records import Call, Evidence, Finding, Investigation, Message, ModelCall, Report, Step
from k8sholmes.reports.base import (
    call_rows,
    evidence_rows,
    finding_rows,
    gather,
    section,
    store_report,
    to_markdown,
)
from k8sholmes.store import Store

KINDS = ("observation", "hypothesis", "conclusion")
COVERAGES = ("observed", "observed_absent", "no_data", "cannot_answer")


def live_summary(store: Store, inv: Investigation) -> dict[str, Any]:
    """While an investigation runs: what is happening and the most useful finding so far (design §10)."""
    findings, evidence, calls, steps = gather(store, inv)
    current = steps[-1] if steps else None
    top = next((f for f in findings if f.state != "accepted_risk"), None)
    return {
        "question": inv.question,
        "status": inv.status,
        "steps": len(steps),
        "calls": len(calls),
        "evidence": len(evidence),
        "now": f"{current.phase}: {current.summary or 'working'}" if current else "not started",
        "top_finding": top.title if top else None,
        "answer": inv.answer,
    }


def _counts(
    findings: list[Finding],
    evidence: dict[str, Evidence],
    calls: list[Call],
    steps: list[Step],
    model_calls: list[ModelCall],
) -> dict[str, int]:
    return {
        "findings": len(findings),
        "accepted": sum(1 for f in findings if f.state == "accepted_risk"),
        **{f"findings_{k}": sum(1 for f in findings if f.kind == k) for k in KINDS},
        "evidence": len(evidence),
        **{f"evidence_{c}": sum(1 for e in evidence.values() if e.coverage == c) for c in COVERAGES},
        "calls": len(calls),
        "calls_refused": sum(1 for c in calls if c.status == "refused"),
        "calls_failed": sum(1 for c in calls if c.status == "failed"),
        "steps": len(steps),
        "model_calls": len(model_calls),
        "prompt_tokens": sum(m.prompt_tokens for m in model_calls),
        "completion_tokens": sum(m.completion_tokens for m in model_calls),
    }


def _model_call_rows(model_calls: list[ModelCall]) -> list[dict[str, Any]]:
    return [
        {
            "phase": m.phase,
            "model": m.model,
            "prompt_tokens": m.prompt_tokens,
            "completion_tokens": m.completion_tokens,
            "duration_ms": m.duration_ms,
            "status": m.status,
        }
        for m in model_calls
    ]


def investigation_report(store: Store, inv: Investigation, *, by: str) -> Report:
    """The final report of an investigation (design §10), as a new revision."""
    findings, evidence, calls, steps = gather(store, inv)
    model_calls = store.find(ModelCall, {"investigation_id": inv.id})
    thread = [
        m for m in store.find(Message, {"investigation_id": inv.id}, order_by="seq") if m.role == "user"
    ]
    numbers = _counts(findings, evidence, calls, steps, model_calls)
    open_findings = [f for f in findings if f.state != "accepted_risk"]
    accepted = [f for f in findings if f.state == "accepted_risk"]
    framing = inv.framing
    scope = (framing.scope.namespaces if framing else inv.scope.namespaces) or [
        "every namespace the permissions allow"
    ]
    period = framing.period.rule if framing and framing.period.rule else inv.period.rule or "not stated"
    gaps = [f"{c}: {n} pieces of evidence" for c in COVERAGES[1:] if (n := numbers[f"evidence_{c}"])]
    question = {
        "question": inv.question,
        "scope": scope,
        "period": period,
        "status": inv.status,
        "started": inv.created_at.isoformat(),
        "model": inv.model_name,
    }
    answer = {
        "answer": inv.answer or "No answer: see the limitations.",
        "top_finding": open_findings[0].title if open_findings else None,
    }
    sections = [
        section("The question", "text", question),
        section("The answer", "text", answer),
        section(
            "Findings", "findings", finding_rows(open_findings, evidence), {"findings": len(open_findings)}
        ),
        section(
            "Accepted as intentional",
            "findings",
            finding_rows(accepted, evidence),
            {"accepted": len(accepted)},
        ),
        section(
            "Limitations and coverage",
            "list",
            [*inv.limitations, *gaps],
            {"limitations": len(inv.limitations)},
        ),
        section("Next steps", "list", sorted({s for f in findings for s in f.next_steps})),
        section("What was read", "table", evidence_rows(evidence), {"evidence": len(evidence)}),
        section(
            "What the model was asked",
            "table",
            _model_call_rows(model_calls),
            {"model_calls": len(model_calls)},
        ),
        section("Knowledge used", "list", sorted({k for f in findings for k in f.knowledge_used})),
        section("The thread", "list", [f"round {m.round}: {m.content}" for m in thread]),
        section("Every call", "table", call_rows(calls), {"calls": len(calls)}),
    ]
    report = Report(
        environment_id=inv.environment_id,
        kind="investigation",
        title=inv.question,
        subject_id=inv.id,
        generated_by=by,
        sections=sections,
        numbers=numbers,
    )
    return store_report(store, report)


# the plain export every surface shares


def investigation_json(store: Store, inv: Investigation) -> dict[str, Any]:
    return {
        "investigation": inv.model_dump(mode="json"),
        "findings": [f.model_dump(mode="json") for f in store.find(Finding, {"investigation_id": inv.id})],
        "steps": [
            s.model_dump(mode="json") for s in store.find(Step, {"investigation_id": inv.id}, order_by="seq")
        ],
        "calls": [c.model_dump(mode="json") for c in store.find(Call, {"investigation_id": inv.id})],
        "evidence": [e.model_dump(mode="json") for e in store.find(Evidence, {"investigation_id": inv.id})],
    }


def report_text(store: Store, inv: Investigation) -> str:
    """The investigation report as Markdown, generated without storing a revision (the command line)."""
    findings, evidence, _, _ = gather(store, inv)
    status = {
        "status": inv.status,
        "model": inv.model_name or "-",
        "steps": inv.current_step,
        "evidence": len(evidence),
    }
    report = Report(
        environment_id=inv.environment_id,
        kind="investigation",
        title=inv.question,
        subject_id=inv.id,
        generated_by="cli",
        sections=[
            section("Status", "text", status),
            section("Answer", "text", {"answer": inv.answer or "No answer: see the limitations."}),
            section("Findings", "findings", finding_rows(findings, evidence), {"findings": len(findings)}),
            section("Limitations", "list", inv.limitations, {"limitations": len(inv.limitations)}),
        ],
        numbers={"findings": len(findings), "evidence": len(evidence), "limitations": len(inv.limitations)},
    )
    influence = [
        f"Influenced by knowledge: {', '.join(f.knowledge_used)}" for f in findings if f.knowledge_used
    ]
    return to_markdown(report) + ("\n".join(influence) + "\n" if influence else "")
