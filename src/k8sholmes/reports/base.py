"""What every report kind shares (design §10): sections and counted numbers, a revision per generation, a
person's edits kept apart from the generated text, the Markdown rendering and view-only share links."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any, cast

from k8sholmes.errors import InvalidInput, NotFound
from k8sholmes.records import (
    Call,
    Evidence,
    Finding,
    Investigation,
    Report,
    ReportEdit,
    ReportKind,
    Share,
    Step,
    now,
)
from k8sholmes.store import Store

Section = dict[str, Any]


def mapping(value: Any) -> dict[str, Any]:
    """A result's data as a mapping; anything else counts as empty."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def section(heading: str, kind: str, body: Any, counted: dict[str, int] | None = None) -> Section:
    return {"heading": heading, "kind": kind, "body": body, "counted": counted or {}}


def finding_rows(findings: list[Finding], evidence: dict[str, Evidence]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in findings:
        rows.append(
            {
                "id": f.id,
                "title": f.title,
                "explanation": f.explanation,
                "kind": f.kind,
                "state": f.state,
                "condition": f.condition,
                "resources": [r.model_dump() for r in f.resources],
                "author": f.author,
                "uncertainty": f.uncertainty,
                "missing_information": f.missing_information,
                "next_steps": f.next_steps,
                "exception_id": f.exception_id,
                "exception_note": f.exception_note,
                "knowledge_used": f.knowledge_used,
                "evidence_for": [_evidence_ref(evidence.get(e), e) for e in f.evidence_for],
                "evidence_against": [_evidence_ref(evidence.get(e), e) for e in f.evidence_against],
            }
        )
    return rows


def _evidence_ref(ev: Evidence | None, ev_id: str) -> dict[str, Any]:
    if ev is None:
        return {"id": ev_id, "missing": True}
    return {
        "id": ev.id,
        "source": ev.source,
        "coverage": ev.coverage,
        "collected_at": ev.collected_at.isoformat(),
        "summary": ev.summary,
        "sha256": ev.sha256,
    }


def gather(
    store: Store, inv: Investigation
) -> tuple[list[Finding], dict[str, Evidence], list[Call], list[Step]]:
    findings = store.find(Finding, {"investigation_id": inv.id}, order_by="created_at")
    evidence = {e.id: e for e in store.find(Evidence, {"investigation_id": inv.id}, order_by="collected_at")}
    calls = store.find(Call, {"investigation_id": inv.id}, order_by="at")
    steps = store.find(Step, {"investigation_id": inv.id}, order_by="seq")
    return findings, evidence, calls, steps


def store_report(
    store: Store,
    *,
    kind: ReportKind,
    environment_id: str,
    subject_id: str,
    title: str,
    sections: list[Section],
    numbers: dict[str, int],
    by: str,
) -> Report:
    earlier = [
        r
        for r in store.find(Report, {"kind": kind, "subject_id": subject_id})
        if r.environment_id == environment_id
    ]
    revision = max((r.revision for r in earlier), default=0) + 1
    report = Report(
        environment_id=environment_id,
        kind=kind,
        title=title,
        subject_id=subject_id,
        revision=revision,
        generated_by=by,
        sections=sections,
        numbers=numbers,
    )
    store.put(report)
    return report


def add_edit(store: Store, report: Report, *, author: str, section: str, text: str) -> Report:
    """A person's addition to a revision; the generated text is never changed (design §10)."""
    if not text.strip():
        raise InvalidInput("an edit needs text")
    if section not in {s["heading"] for s in report.sections}:
        raise InvalidInput(f"the report has no section {section!r}")
    edited = report.model_copy(
        update={"edits": [*report.edits, ReportEdit(author=author, section=section, text=text.strip())]}
    )
    store.put(edited)
    return edited


def to_markdown(report: Report) -> str:
    """The report as Markdown: generated text first, a person's edits quoted after their section."""
    lines = [
        f"# {report.title}",
        "",
        f"Revision {report.revision}, generated {report.generated_at.isoformat(timespec='minutes')} by "
        f"{report.generated_by}. Every number below was counted from stored records.",
        "",
    ]
    for sec in report.sections:
        lines += [f"## {sec['heading']}", "", *_markdown_body(sec["kind"], sec["body"])]
        for edit in [e for e in report.edits if e.section == sec["heading"]]:
            lines += ["", f"> Edit by {edit.author} on {edit.at.isoformat(timespec='minutes')}: {edit.text}"]
        lines.append("")
    if report.numbers:
        lines += ["## Numbers", "", *[f"- {k}: {v}" for k, v in report.numbers.items()], ""]
    return "\n".join(lines)


def _markdown_body(kind: str, body: Any) -> list[str]:
    if kind == "text" and isinstance(body, dict):
        return [f"- **{k}**: {v}" for k, v in cast(dict[str, Any], body).items() if v not in (None, "", [])]
    if kind == "list":
        return [f"- {item}" for item in cast(list[Any], body)] or ["(nothing)"]
    if kind == "findings":
        return _markdown_findings(cast(list[dict[str, Any]], body))
    rows = cast(list[dict[str, Any]], body)
    if not rows:
        return ["(none)"]
    keys = list(rows[0].keys())

    def cell(row: dict[str, Any], key: str) -> str:
        return str(row.get(key, "")).replace("|", "/").replace("\n", " ")

    return [
        "| " + " | ".join(keys) + " |",
        "|" + "---|" * len(keys),
        *["| " + " | ".join(cell(r, k) for k in keys) + " |" for r in rows],
    ]


def _markdown_findings(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for f in rows:
        lines.append(f"- **{f['title']}** ({f['kind']}, {f['condition']}, {f['state']}) by {f['author']}")
        lines.append(f"  {f['explanation']}")
        for e in cast(list[dict[str, Any]], f["evidence_for"]):
            lines.append(
                f"  Evidence: {e['id']} ({e.get('source')}, {e.get('coverage')}, {e.get('collected_at')})"
            )
    return lines or ["(none)"]


def create_share(store: Store, report: Report, *, by: str, days: int = 7) -> tuple[Share, str]:
    """A view-only link: the token is returned once; the store keeps its hash (design §10)."""
    token = secrets.token_urlsafe(24)
    share = Share(
        report_id=report.id,
        environment_id=report.environment_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=by,
        expires_at=now() + timedelta(days=days),
    )
    store.put(share)
    return share, token


def shared_report(store: Store, token: str) -> Report:
    found = store.find(Share, {"token_hash": hashlib.sha256(token.encode()).hexdigest()})
    if not found or found[0].revoked_at is not None or found[0].expires_at <= now():
        raise NotFound("this share link does not exist, expired or was revoked")
    return store.get(Report, found[0].report_id)


def revoke_share(store: Store, share: Share) -> Share:
    revoked = share.model_copy(update={"revoked_at": now()})
    store.put(revoked)
    return revoked
