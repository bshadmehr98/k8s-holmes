# Extensions

Two ways to add a capability (design §5.5). Both are loaded when K8sHolmes starts and appear in the
catalog with their origin; an extension that fails to load is listed as broken with its error, and nothing
else is affected. Extensions go through the same gate as the built-ins: the user's role, the environment's
settings and the namespace allow-list decide whether a call runs.

## A Python entry point

Declare an entry point in the group `k8sholmes.capabilities` that returns `Capability` objects:

```text
# pyproject.toml of your package
[project.entry-points."k8sholmes.capabilities"]
mycompany = "mycompany.k8sholmes:capabilities"
```

```python
from pydantic import BaseModel

from k8sholmes.capabilities import Capability, Context
from k8sholmes.evidence import Result


class Inputs(BaseModel):
    namespace: str


def run(inputs: BaseModel, ctx: Context) -> Result:
    assert isinstance(inputs, Inputs)
    pods = ctx.cluster.list("pods", namespace=inputs.namespace)
    return Result(
        summary=f"{len(pods)} pods in {inputs.namespace}",
        data={"count": len(pods)},
        coverage="observed",
        coverage_reason="counted from the pod list",
    )


def capabilities() -> list[Capability]:
    return [
        Capability(
            id="mycompany.pod_count",
            title="Pod count",
            purpose="How many pods a namespace runs right now.",
            class_="inspect",
            systems=("kubernetes",),
            inputs=Inputs,
            permissions=(("list", "pods"),),
            run=run,
            coverage=("observed",),
        )
    ]


assert capabilities()[0].id == "mycompany.pod_count"
```

Rules for a capability: read through `ctx.cluster` (the `Cluster` port), never run `kubectl` yourself; set
the coverage honestly (`observed`, `observed_absent`, `no_data`, `cannot_answer`); return data, not
instructions; raise for a failure and the registry records it as a failed call with `no_data` or
`cannot_answer`. The redactor runs on your result before storage and before the model sees it.

## A command capability

An administrator declares one in `$K8SHOLMES_HOME/capabilities/<name>.toml`. The executable must be on the
allow-list `$K8SHOLMES_HOME/allowed-executables.txt` (one path per line); the argument template's
placeholders are the validated inputs; nothing passes through a shell.

```text
[[capability]]
id = "ops.disk_usage"
title = "Disk usage of a node"
purpose = "Free space on a node's root volume, from the ops script."
executable = "/usr/local/bin/node-disk"
args = ["--node", "{node}", "--json"]
timeout_ms = 10000
class = "inspect"          # inspect | probe | change
systems = ["node-disk"]
coverage = "observed"      # the statement a successful run makes

[capability.inputs.node]
type = "string"            # string | int | float | bool
description = "The node's name."
# default = "..."          # optional; without it the input is required
```

The output is captured as text, or as JSON when it parses. A non-zero exit is a failed call with
`no_data`, the exit code and the tail of stderr. The catalog shows the capability with origin
`command <file>`.

## Testing an extension

Build a `FakeCluster` from `k8sholmes.testing`, a `Registry` with your capability and a `Context`, and
call `registry.execute(id, inputs, ctx, auth)`: you get the `Call` and the `Evidence` as an investigation
would. `examples/command_capability.py` does exactly that for a command capability.
