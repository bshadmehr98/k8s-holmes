"""Every example runs, offline, and is listed in examples/README.md (CONTRIBUTING.md, "Tests")."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_example_runs_and_is_listed(example: Path, tmp_path: Path) -> None:
    doc = example.read_text()
    assert "**What:**" in doc and "**How:**" in doc and "**Why:**" in doc, (
        "the docstring explains what, how and why"
    )
    assert f"[`{example.name}`]({example.name})" in (ROOT / "examples" / "README.md").read_text()
    result = subprocess.run(
        [sys.executable, str(example)], cwd=tmp_path, capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip(), "an example prints what it shows"


def test_the_quickstart_exists_for_the_wheel_smoke_test() -> None:
    assert (ROOT / "examples" / "quickstart.py").exists()
