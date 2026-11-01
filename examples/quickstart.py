"""Quickstart: investigate the demo environment offline and read the report.

**What:** the whole loop without a cluster or a model: the recorded demo environment, the rule-based
offline investigator, an investigation, its findings with their evidence, the Markdown report.
**How:** `App.open(home)` on a temporary home, `App.setup_demo()`, `Engine.start` and `Engine.run`,
`reports.report_text`.
**Why:** to see what an investigation records before connecting your own cluster and model. This file
runs with only the `k8sholmes` wheel installed (scripts/check.sh runs it that way).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from k8sholmes.app import App
from k8sholmes.records import Evidence, Finding
from k8sholmes.reports import report_text

with tempfile.TemporaryDirectory() as tmp:
    app = App.open(Path(tmp) / "home", extensions=False)
    env, user = app.setup_demo()  # the recorded shop, the demo model endpoint, the user `demo`

    inv = app.engine.start(environment=env, question="Why is api restarting?", user=user)
    inv = app.engine.run(inv.id, user)

    findings = app.store.find(Finding, {"investigation_id": inv.id})
    print(report_text(app.store, inv))

    assert inv.status == "completed", inv.limitations
    assert any(f.condition == "pod.crashloop.oom" for f in findings)
    for finding in findings:  # every finding cites evidence that exists (guarantee 3)
        for evidence_id in finding.evidence_for:
            assert app.store.get(Evidence, evidence_id).investigation_id == inv.id
    print(f"\n{len(findings)} findings, each citing stored evidence; status {inv.status}.")
