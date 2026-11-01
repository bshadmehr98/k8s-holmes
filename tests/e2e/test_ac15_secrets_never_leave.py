"""Guarantee 15: planted secrets (an env value, a log line, an argument) reach no evidence, model message,
report, export or terminal output; the redaction count says how many were removed."""

import json
from pathlib import Path
from typing import Any

from k8sholmes import records, reports
from k8sholmes import saved as saved_mod
from k8sholmes.cli import main
from k8sholmes.records import Evidence
from k8sholmes.reports import report_text
from k8sholmes.testing import ScriptedModel
from k8sholmes.testing.demo import PLANTED_BEARER, PLANTED_TOKEN
from k8sholmes.testing.model import calls, structured, text
from tests.e2e.conftest import Demo

PLANTED = (PLANTED_TOKEN, PLANTED_BEARER)
FRAME = {
    "question": "secrets",
    "namespaces": ["shop"],
    "resources": [],
    "period_hours": 24,
    "hypotheses": [],
    "intended_capabilities": [],
}


def clean(blob: str) -> bool:
    return not any(p in blob for p in PLANTED)


def test_planted_secrets_appear_nowhere(demo: Demo, tmp_path: Path, capsys) -> None:
    seen: list[str] = []

    def script(messages: list[dict[str, Any]], tools, schema):
        seen.append(json.dumps(messages))
        if schema and schema.get("title") == "framing":
            return structured(FRAME)
        if schema:
            evidence = demo.app.store.find(Evidence)
            get_ev = next(e for e in evidence if e.source == "k8s.get")
            return structured(
                {
                    "answer": "the worker's configuration was read",
                    "findings": [
                        {
                            "title": "worker configuration read",
                            "explanation": "The worker deployment was read.",
                            "kind": "observation",
                            "condition": "custom.read",
                            "resources": [{"kind": "Deployment", "namespace": "shop", "name": "worker"}],
                            "evidence_for": [get_ev.id],
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
        if not any(m.get("role") == "tool" for m in messages):
            return calls(
                (
                    "k8s.get",
                    {"kind": "deployment", "name": "worker", "namespace": "shop"},
                ),  # the env value and the argument
                (
                    "k8s.logs",
                    {"pod": "web-5f0c-k2m8p", "namespace": "shop", "container": "web"},
                ),  # the log line
            )
        return text("done")

    demo.app.engine.model_for = lambda inv: ScriptedModel(script=script)
    inv = demo.ask("Why is worker unhealthy?")
    assert inv.status == "completed", inv.limitations
    evidence = demo.app.store.find(Evidence, {"investigation_id": inv.id})
    assert {e.source for e in evidence} >= {"k8s.get", "k8s.logs", "k8s.inventory"}
    assert clean(json.dumps([e.model_dump(mode="json") for e in evidence], default=str))
    by_source = {e.source: e for e in evidence}
    assert by_source["k8s.get"].redactions >= 2  # the EXPORT_TOKEN value and the --export-token argument
    assert by_source["k8s.logs"].redactions >= 1  # the Authorization header in the log
    assert seen, "the model was called"
    assert all(clean(s) for s in seen), "what the model saw"
    assert clean(report_text(demo.app.store, inv))
    for report in (
        reports.investigation_report(demo.app.store, inv, by="user:t"),
        reports.incident_review(demo.app.store, inv, by="user:t"),
    ):
        assert clean(reports.to_markdown(report)) and clean(report.model_dump_json())
    saved = saved_mod.save(
        demo.app.store,
        records.SavedInvestigation(
            environment_id=demo.env.id, question="Why is worker unhealthy?", created_by=demo.user.id
        ),
    )
    demo.app.store.put(saved.model_copy(update={"runs": [inv.id]}))
    comparison = saved_mod.compare_runs(
        demo.app.store, demo.app.store.get(records.SavedInvestigation, saved.id), inv.id
    )
    assert clean(
        saved_mod.comparison_report(demo.app.store, saved, comparison, by="user:t").model_dump_json()
    )
    assert clean(
        json.dumps(
            {"text": f"K8sHolmes: {saved.question}\n{comparison.summary}", "summary": comparison.summary}
        )
    )
    out = tmp_path / "export"
    assert main(["--home", str(demo.app.home), "export", inv.id, "--out", str(out)]) == 0
    assert clean("".join(p.read_text() for p in out.rglob("*") if p.is_file()))
    captured = capsys.readouterr()
    assert clean(captured.out + captured.err)


def test_the_cli_investigation_output_is_clean(demo: Demo, capsys) -> None:
    code = main(
        [
            "--home",
            str(demo.app.home),
            "investigate",
            "Why is worker unhealthy?",
            "--env",
            "demo",
            "--user",
            "demo",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "worker" in captured.out
    assert clean(captured.out + captured.err)
