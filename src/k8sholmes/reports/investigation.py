"""The live summary and the investigation report (design §10.1, §10.2), and the plain export the command
line and the API share."""

from __future__ import annotations

from typing import Any

from k8sholmes.records import Call, Evidence, Finding, Investigation, Message, ModelCall, Report, Step
from k8sholmes.reports.base import finding_rows, gather, section, store_report, to_markdown
from k8sholmes.store import Store


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


def investigation_report(store: Store, inv: Investigation, *, by: str) -> Report:
    """The final report of an investigation (design §10), as a new revision."""
    findings, evidence, calls, steps = gather(store, inv)
    model_calls = store.find(ModelCall, {"investigation_id": inv.id})
    thread = [
        m for m in store.find(Message, {"investigation_id": inv.id}, order_by="seq") if m.role == "user"
    ]
    accepted = [f for f in findings if f.state == "accepted_risk"]
    by_kind = {
        k: sum(1 for f in findings if f.kind == k) for k in ("observation", "hypothesis", "conclusion")
    }
    by_coverage = {
        c: sum(1 for e in evidence.values() if e.coverage == c)
        for c in ("observed", "observed_absent", "no_data", "cannot_answer")
    }
    refused = [c for c in calls if c.status == "refused"]
    failed = [c for c in calls if c.status == "failed"]
    numbers = {
        "findings": len(findings),
        "accepted": len(accepted),
        **{f"findings_{k}": v for k, v in by_kind.items()},
        "evidence": len(evidence),
        **{f"evidence_{k}": v for k, v in by_coverage.items()},
        "calls": len(calls),
        "calls_refused": len(refused),
        "calls_failed": len(failed),
        "steps": len(steps),
        "model_calls": len(model_calls),
        "prompt_tokens": sum(m.prompt_tokens for m in model_calls),
        "completion_tokens": sum(m.completion_tokens for m in model_calls),
    }
    scope = (inv.framing.scope.namespaces if inv.framing else inv.scope.namespaces) or [
        "every namespace the permissions allow"
    ]
    period = inv.framing.period.rule if inv.framing and inv.framing.period.rule else "not stated"
    sections = [
        section(
            "The question",
            "text",
            {
                "question": inv.question,
                "scope": scope,
                "period": period,
                "status": inv.status,
                "started": inv.created_at.isoformat(),
                "model": inv.model_name,
            },
        ),
        section(
            "The answer",
            "text",
            {
                "answer": inv.answer or "No answer: see the limitations.",
                "top_finding": findings[0].title if findings else None,
            },
        ),
        section(
            "Findings",
            "findings",
            finding_rows([f for f in findings if f.state != "accepted_risk"], evidence),
            {"findings": len(findings) - len(accepted)},
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
            [
                *inv.limitations,
                *[f"{c}: {n} pieces of evidence" for c, n in by_coverage.items() if c != "observed" and n],
            ],
            {"limitations": len(inv.limitations)},
        ),
        section("Next steps", "list", sorted({s for f in findings for s in f.next_steps})),
        section(
            "What was read",
            "table",
            [
                {
                    "source": e.source,
                    "coverage": e.coverage,
                    "collected_at": e.collected_at.isoformat(),
                    "summary": e.summary,
                    "redactions": e.redactions,
                }
                for e in evidence.values()
            ],
            {"evidence": len(evidence)},
        ),
        section(
            "What the model was asked",
            "table",
            [
                {
                    "phase": m.phase,
                    "model": m.model,
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                    "duration_ms": m.duration_ms,
                    "status": m.status,
                }
                for m in model_calls
            ],
            {"model_calls": len(model_calls)},
        ),
        section("Knowledge used", "list", sorted({k for f in findings for k in f.knowledge_used})),
        section("The thread", "list", [f"round {m.round}: {m.content}" for m in thread]),
        section(
            "Every call",
            "table",
            [
                {
                    "capability": c.capability_id,
                    "status": c.status,
                    "reason": c.reason,
                    "duration_ms": c.duration_ms,
                    "at": c.at.isoformat(),
                }
                for c in calls
            ],
            {"calls": len(calls)},
        ),
    ]
    return store_report(
        store,
        kind="investigation",
        environment_id=inv.environment_id,
        subject_id=inv.id,
        title=inv.question,
        sections=sections,
        numbers=numbers,
        by=by,
    )


def investigation_json(app: Any, inv: Investigation) -> dict[str, Any]:
    store: Store = app.store
    return {
        "investigation": inv.model_dump(mode="json"),
        "findings": [f.model_dump(mode="json") for f in store.find(Finding, {"investigation_id": inv.id})],
        "steps": [
            s.model_dump(mode="json") for s in store.find(Step, {"investigation_id": inv.id}, order_by="seq")
        ],
        "calls": [c.model_dump(mode="json") for c in store.find(Call, {"investigation_id": inv.id})],
        "evidence": [e.model_dump(mode="json") for e in store.find(Evidence, {"investigation_id": inv.id})],
    }


def report_text(app: Any, inv: Investigation) -> str:
    """The investigation report as Markdown, generated without storing a revision (the command line)."""
    store: Store = app.store
    findings, evidence, _, _ = gather(store, inv)
    report = Report(
        environment_id=inv.environment_id,
        kind="investigation",
        title=inv.question,
        subject_id=inv.id,
        generated_by="cli",
        sections=[
            section(
                "Status",
                "text",
                {
                    "status": inv.status,
                    "model": inv.model_name or "-",
                    "steps": inv.current_step,
                    "evidence": len(evidence),
                },
            ),
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
