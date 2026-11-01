"""Reports built from the store (design §10). Milestone 3 brings the five kinds; this holds the plain-text
report and the export shape the command line and the API share today."""

from __future__ import annotations

from typing import Any

from k8sholmes.app import App
from k8sholmes.records import Call, Evidence, Finding, Investigation, Step


def investigation_json(app: App, inv: Investigation) -> dict[str, Any]:
    return {
        "investigation": inv.model_dump(mode="json"),
        "findings": [
            f.model_dump(mode="json") for f in app.store.find(Finding, {"investigation_id": inv.id})
        ],
        "steps": [
            s.model_dump(mode="json")
            for s in app.store.find(Step, {"investigation_id": inv.id}, order_by="seq")
        ],
        "calls": [c.model_dump(mode="json") for c in app.store.find(Call, {"investigation_id": inv.id})],
        "evidence": [
            e.model_dump(mode="json") for e in app.store.find(Evidence, {"investigation_id": inv.id})
        ],
    }


def report_text(app: App, inv: Investigation) -> str:
    """The investigation as plain text (the Markdown report kinds of design §10 arrive with milestone 3)."""
    findings = app.store.find(Finding, {"investigation_id": inv.id})
    evidence = {e.id: e for e in app.store.find(Evidence, {"investigation_id": inv.id})}
    lines = [
        f"# {inv.question}",
        "",
        f"Status: {inv.status}. Model: {inv.model_name or '-'}. Steps: {inv.current_step}. "
        f"Evidence: {len(evidence)}.",
        "",
    ]
    if inv.answer:
        lines += ["## Answer", "", inv.answer, ""]
    if findings:
        lines += ["## Findings", ""]
        for f in sorted(findings, key=lambda f: (f.state != "open", f.kind, f.title)):
            state = f" [{f.state}" + (f", exception {f.exception_id}" if f.exception_id else "") + "]"
            lines.append(f"- **{f.title}** ({f.kind}, {f.condition}){state}")
            lines.append(f"  {f.explanation}")
            cited = ", ".join(
                f"{e} ({evidence[e].source}, {evidence[e].coverage})" if e in evidence else e
                for e in f.evidence_for
            )
            lines.append(f"  Evidence: {cited}")
            if f.uncertainty:
                lines.append(f"  Uncertainty: {f.uncertainty}")
            if f.exception_note:
                lines.append(f"  Note: {f.exception_note}")
            if f.knowledge_used:
                lines.append(f"  Influenced by knowledge: {', '.join(f.knowledge_used)}")
            for step in f.next_steps:
                lines.append(f"  Next: {step}")
        lines.append("")
    if inv.limitations:
        lines += ["## Limitations", "", *[f"- {lim}" for lim in inv.limitations], ""]
    return "\n".join(lines)


__all__ = ["investigation_json", "report_text"]
