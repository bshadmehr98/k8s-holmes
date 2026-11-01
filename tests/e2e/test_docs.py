"""The documentation stays runnable and its links resolve (CONTRIBUTING.md, "Tests").

Every ``python`` block in README.md and docs/*.md is executed; blocks that must not run use a ``text``
fence. Every relative link in the Markdown pages points at a file that exists.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNABLE = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINKED = [
    *RUNNABLE,
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CHANGELOG.md",
    *sorted((ROOT / "design").rglob("*.md")),
    *sorted((ROOT / "examples").glob("*.md")),
]
BLOCK = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def python_blocks(page: Path) -> list[str]:
    return BLOCK.findall(page.read_text())


@pytest.mark.parametrize("page", RUNNABLE, ids=lambda p: str(p.relative_to(ROOT)))
def test_python_blocks_run(page: Path, tmp_path: Path) -> None:
    for n, code in enumerate(python_blocks(page)):
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{page.name} block {n} failed:\n{result.stderr}"


@pytest.mark.parametrize("page", LINKED, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(page: Path) -> None:
    missing = []
    for target in LINK.findall(page.read_text()):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#")[0]
        if path and not (page.parent / path).exists():
            missing.append(target)
    assert not missing, f"{page.relative_to(ROOT)} links to files that don't exist: {missing}"


def test_the_capabilities_page_lists_every_built_in() -> None:
    from k8sholmes.capabilities.builtin import builtin_registry

    page = (ROOT / "docs" / "capabilities.md").read_text()
    missing = [
        cap.id for cap in builtin_registry(extensions=False)._by_id.values() if f"`{cap.id}`" not in page
    ]
    assert not missing, f"docs/capabilities.md does not list {missing}"
