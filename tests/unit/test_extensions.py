"""Extensions (design §5.5): command capabilities from TOML, with the allow-list, and broken files reported."""

import sys
from pathlib import Path

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context, Registry
from k8sholmes.capabilities.extensions import load_command_capabilities
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster

TOML = """
[[capability]]
id = "shell.disk_free"
title = "Disk free on the K8sHolmes host"
purpose = "df -h for one path."
class = "inspect"
systems = ["host"]
executable = "{python}"
args = ["-c", "import json, shutil; print(json.dumps(shutil.disk_usage('{{path}}')._asdict()))"]
timeout_ms = 5000
coverage = "observed"

[capability.inputs.path]
type = "string"
description = "The path to measure."
default = "/"
"""


def test_command_capability_runs_with_validated_inputs_and_no_shell(tmp_path: Path) -> None:
    folder = tmp_path / "capabilities"
    folder.mkdir()
    (folder / "disk.toml").write_text(TOML.format(python=sys.executable))
    registry = Registry()
    load_command_capabilities(registry, folder, allowed_executables=[sys.executable])
    assert registry.broken == {}
    cap = registry.get("shell.disk_free")
    assert cap.origin == "command disk.toml"
    assert cap.class_ == "inspect"
    assert cap.describe()["inputs"]["properties"]["path"]["default"] == "/"
    store = Store(tmp_path / "db")
    auth = Auth(store)
    env = records.Environment(name="e", context="c")
    user = auth.create_user(
        "u", "a strong one", grants=[records.Grant(environment_id=env.id, role="investigator")]
    )
    ctx = Context(
        environment=env, cluster=FakeCluster(), store=store, user=user, settings=records.CapabilitySettings()
    )
    call, evidence = registry.execute("shell.disk_free", {"path": str(tmp_path)}, ctx, auth)
    assert call.status == "completed", call.reason
    assert evidence is not None
    assert evidence.data["total"] > 0
    call, _ = registry.execute("shell.disk_free", {"path": 5}, ctx, auth)
    assert call.status == "invalid"


def test_disallowed_executables_and_broken_files_are_reported(tmp_path: Path) -> None:
    folder = tmp_path / "capabilities"
    folder.mkdir()
    (folder / "disk.toml").write_text(TOML.format(python=sys.executable))
    (folder / "broken.toml").write_text("this is not toml = = =")
    registry = Registry()
    load_command_capabilities(registry, folder, allowed_executables=["/usr/bin/true"])
    assert registry.all() == []
    assert "not in the allow-list" in registry.broken["command disk.toml"]
    assert "command broken.toml" in registry.broken
    load_command_capabilities(registry, tmp_path / "missing", allowed_executables=[])
