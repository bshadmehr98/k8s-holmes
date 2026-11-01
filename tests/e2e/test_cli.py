"""The command line: demo, investigate, capabilities, environments, export; exit codes."""

import json
from pathlib import Path

from k8sholmes.cli import main


def test_demo_investigate_capabilities_and_export(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    assert main(["--home", str(home), "demo"]) == 0
    assert "demo environment ready" in capsys.readouterr().out
    assert main(["--home", str(home), "environments"]) == 0
    assert "demo\trecording" in capsys.readouterr().out
    assert main(["--home", str(home), "capabilities", "--env", "demo"]) == 0
    out = capsys.readouterr().out
    assert "k8s.workload_health" in out
    assert "http.probe_url" in out and "unavailable: http.probe_url is a probe" in out
    assert (
        main(
            [
                "--home",
                str(home),
                "investigate",
                "Why is api restarting?",
                "--env",
                "demo",
                "-n",
                "shop",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["investigation"]["status"] == "completed"
    conditions = {f["condition"] for f in payload["findings"]}
    assert "pod.crashloop.oom" in conditions
    inv_id = payload["investigation"]["id"]
    assert main(["--home", str(home), "investigate", "Is this environment isolated?", "--env", "demo"]) == 0
    text = capsys.readouterr()
    assert "## Findings" in text.out and "## Limitations" in text.out
    assert "[framing]" in text.err
    out_dir = tmp_path / "export"
    assert main(["--home", str(home), "export", inv_id, "--out", str(out_dir)]) == 0
    assert (out_dir / "report.md").exists()
    exported = json.loads((out_dir / "report.json").read_text())
    assert exported["format_version"] == "1"
    assert len(list((out_dir / "evidence").glob("*.json"))) == len(exported["evidence"])


def test_failures_exit_1_with_a_message(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    assert main(["--home", str(home), "investigate", "x", "--env", "nope"]) == 1
    assert "no environment named 'nope'" in capsys.readouterr().err
    assert main(["--home", str(home), "demo", "--question", "Which services appear unused?"]) == 0
    assert "legacy-export" in capsys.readouterr().out
