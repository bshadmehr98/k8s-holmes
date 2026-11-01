"""The core imports nothing optional (CONTRIBUTING.md, "Code conventions")."""

import subprocess
import sys


def test_core_import_pulls_no_adapters_or_extras() -> None:
    code = (
        "import sys, k8sholmes\n"
        "bad = [m for m in sys.modules if m.startswith(('k8sholmes.adapters', 'kubernetes', 'hone_models',"
        " 'httpx', 'typer', 'rich'))]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
