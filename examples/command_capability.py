"""A command capability from TOML, run through the gate.

**What:** a capability declared in a TOML file that runs an allow-listed executable with an argument
template; registered, described in the catalog with its origin, and executed into a `Call` and `Evidence`.
**How:** `k8sholmes.capabilities.extensions.load_command_capabilities`, a `Registry`, a `Context` over a
`FakeCluster`, `Registry.execute`.
**Why:** to wire your own scripts into investigations without writing Python, and to see what the
allow-list refuses.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from k8sholmes import records
from k8sholmes.auth import Auth
from k8sholmes.capabilities import Context, Registry
from k8sholmes.capabilities.extensions import load_command_capabilities
from k8sholmes.store import Store
from k8sholmes.testing import FakeCluster

ECHO = shutil.which("echo") or "/bin/echo"
GREET = f'''
[[capability]]
id = "ops.greet"
title = "Greet a namespace"
purpose = "An example command capability: echoes the namespace it was asked about."
executable = "{ECHO}"
args = ["namespace", "{{namespace}}"]
timeout_ms = 5000
class = "inspect"
systems = ["echo"]
coverage = "observed"

[capability.inputs.namespace]
type = "string"
description = "The namespace to greet."
'''
FORBIDDEN = """
[[capability]]
id = "ops.forbidden"
title = "Not allowed"
purpose = "Its executable is not on the allow-list: the whole file is reported as broken."
executable = "/usr/bin/env"
args = []
"""

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "capabilities"
    folder.mkdir()
    (folder / "ops.toml").write_text(GREET)
    (folder / "forbidden.toml").write_text(FORBIDDEN)
    registry = Registry()
    load_command_capabilities(registry, folder, allowed_executables=[ECHO])

    print("loaded:", [c["id"] for c in (cap.describe() for cap in registry._by_id.values())])
    print("broken:", registry.broken)
    assert registry.get("ops.greet").origin == "command ops.toml"
    assert "ops.forbidden" not in registry._by_id
    assert "allow-list" in registry.broken["command forbidden.toml"]

    store = Store(Path(tmp) / "k8sholmes.db")
    auth = Auth(store)
    env = records.Environment(name="lab", context="lab")
    store.put(env)
    user = auth.create_user(
        "me", "a strong one", grants=[records.Grant(environment_id=env.id, role="investigator")]
    )
    ctx = Context(environment=env, cluster=FakeCluster(), store=store, user=user)

    call, evidence = registry.execute("ops.greet", {"namespace": "shop"}, ctx, auth)
    print(
        "call:",
        call.status,
        "| evidence:",
        evidence.summary if evidence else None,
        evidence.data if evidence else None,
    )
    assert call.status == "completed" and evidence is not None
    assert evidence.data.strip() == "namespace shop" and evidence.coverage == "observed"

    call, _ = registry.execute("ops.greet", {"namespace": 5, "extra": True}, ctx, auth)
    print("bad inputs:", call.status, "|", call.reason)
    assert call.status == "invalid" and "extra" in call.reason  # inputs are validated (guarantee 2)
