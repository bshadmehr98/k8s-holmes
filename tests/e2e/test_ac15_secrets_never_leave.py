"""Guarantee 15: planted secrets reach no evidence, no model message, no report and no export."""

import json
from pathlib import Path

from k8sholmes.cli import main
from k8sholmes.records import Evidence
from k8sholmes.testing.demo import PLANTED_BEARER, PLANTED_TOKEN
from tests.e2e.conftest import Demo


def test_planted_secrets_appear_nowhere(demo: Demo, tmp_path: Path, capsys) -> None:
    inv = demo.ask("Why is worker unhealthy?")  # reads the worker, whose env holds the planted token
    demo.app.engine.start(environment=demo.env, question="x", user=demo.user)
    evidence = demo.app.store.find(Evidence, {"investigation_id": inv.id})
    assert evidence
    blob = json.dumps([e.model_dump(mode="json") for e in evidence], default=str)
    assert PLANTED_TOKEN not in blob
    assert PLANTED_BEARER not in blob
    assert sum(e.redactions for e in evidence) >= 1
    model = demo.app.model_for(inv)
    # what the model saw: replay through the engine's message builder
    messages = demo.app.engine._messages(inv, demo.env, demo.app.engine._available(demo.env, demo.user))
    assert PLANTED_TOKEN not in json.dumps(messages)
    assert PLANTED_BEARER not in json.dumps(messages)
    assert model.name == "demo-investigator"
    out = tmp_path / "export"
    assert main(["--home", str(demo.app.home), "export", inv.id, "--out", str(out)]) == 0
    exported = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert PLANTED_TOKEN not in exported
    assert PLANTED_BEARER not in exported
    captured = capsys.readouterr()
    assert PLANTED_TOKEN not in captured.out + captured.err
