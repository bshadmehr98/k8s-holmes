"""From an investigation to a report, a proposal, its verification and a saved comparison.

**What:** the follow-through of design §10 to §12 on the demo: a stored report revision with a person's
edit, a proposal verified against the recorded "after the fix" state, and two saved runs compared by
finding key.
**How:** `reports.investigation_report`, `reports.add_edit`, `reports.to_markdown`;
`proposals.create_proposal`, `proposals.set_state`, `proposals.verify`; `saved.save`, `saved.start_run`,
`saved.compare_runs`.
**Why:** to see that every number in a report is a count of records, that `verified` is only ever set by a
rerun that observes the condition absent, and that a rerun reports what changed, not what stayed the same.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from k8sholmes import proposals, reports
from k8sholmes.app import App
from k8sholmes.capabilities import Context
from k8sholmes.records import Finding, Proposal, SavedInvestigation
from k8sholmes.saved import compare_runs, save, start_run

with tempfile.TemporaryDirectory() as tmp:
    app = App.open(Path(tmp) / "home", extensions=False)
    env, user = app.setup_demo()
    by = f"user:{user.id}"

    # a saved investigation, run once before and once after the fix
    saved = save(
        app.store,
        SavedInvestigation(environment_id=env.id, question="Why is api restarting?", created_by=user.id),
    )
    first = start_run(app.store, app.engine, saved, env, user)
    app.engine.run(first.id, user)

    # a report with a human edit, apart from the generated text
    report = reports.investigation_report(app.store, first, by=by)
    report = reports.add_edit(
        app.store, report, author=by, section="The answer", text="Confirmed with the on-call engineer."
    )
    print(reports.to_markdown(report)[:600], "…\n")
    assert report.numbers["findings"] == app.store.count(Finding, {"investigation_id": first.id})

    # a proposal, applied by a person, verified by a rerun
    oom = next(
        f
        for f in app.store.find(Finding, {"investigation_id": first.id})
        if f.condition == "pod.crashloop.oom"
    )
    proposal = proposals.create_proposal(
        app.store,
        Proposal(
            environment_id=env.id,
            investigation_id=first.id,
            finding_ids=[oom.id],
            kind="config_change",
            title="Raise api's memory limit to 512Mi",
            change="resources.limits.memory: 256Mi -> 512Mi on Deployment shop/api",
            intended_effect="the api containers stop being OOM-killed",
            verification_capabilities=[
                {
                    "id": "k8s.workload_health",
                    "inputs": {"kind": "Deployment", "namespace": "shop", "name": "api"},
                }
            ],
            verification_condition="pod.crashloop.oom",
            author=by,
        ),
    )
    proposal = proposals.set_state(app.store, proposal, "approved", by=by)
    proposal = proposals.set_state(
        app.store, proposal, "applied", by=by, note="kubectl set resources, by hand"
    )
    ctx = Context(environment=env, cluster=app.cluster_for(env), store=app.store, user=user)
    proposal, _ = proposals.verify(app.registry, app.auth, ctx, proposal)
    print("before the fix:", proposal.verifications[-1].outcome, "-", proposal.verifications[-1].detail)
    assert proposal.verifications[-1].outcome == "not_verified"

    app.setup_demo(after_fix=True)  # the recorded state after the limit was raised
    ctx = Context(
        environment=env, cluster=app.cluster_for(env), store=app.store, user=user
    )  # reads the new recording
    proposal, _ = proposals.verify(app.registry, app.auth, ctx, proposal)
    print("after the fix: ", proposal.verifications[-1].outcome, "-", proposal.verifications[-1].detail)
    assert proposal.state == "verified" and app.store.get(Finding, oom.id).state == "verified"

    # the second run, compared with the first by finding key
    saved = app.store.get(SavedInvestigation, saved.id)
    second = start_run(app.store, app.engine, saved, env, user)
    app.engine.run(second.id, user)
    comparison = compare_runs(app.store, app.store.get(SavedInvestigation, saved.id), second.id)
    print("comparison:", comparison.summary)
    assert oom.id in comparison.resolved
