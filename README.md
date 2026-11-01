# k8sholmes

A detective for Kubernetes clusters. Point it at a pod that keeps restarting, a deployment that will not
roll out or a service nobody can reach; it gathers the evidence read-only, runs the known failure patterns
against it, reasons over what it found, and writes a case file: what is wrong, why, the evidence for each
claim, and the exact commands that would fix it. It never runs them.

**Status: proposed, nothing built.** The first design is written and waits for the owner:
[design/changes/0001-initial-design.md](design/changes/0001-initial-design.md). Start with
[design/README.md](design/README.md) for why, and [design/current.md](design/current.md) for how.

## What it will do

- **Gather evidence like an engineer would**, in seconds and read-only: the pod, its events, its logs, its
  owner chain, the node, the volumes, the config it references; a service's endpoints and the pods its
  selector really matches.
- **Run the known failure patterns as code**: OOMKilled, image not found or unauthorized, Pending for
  resources, affinity or taints, a probe on the wrong port, a missing ConfigMap key, a Service whose
  selector matches nothing, a PVC without a provisioner, a stuck rollout, a node under pressure, and more.
  Deterministic, offline, with stable ids.
- **Ask a model once**, over the whole redacted case, to connect the findings into a root cause; check
  its answer against the evidence; keep exactly what it was sent.
- **Write a case file** you can open a week later without the cluster: evidence as returned with hashes,
  findings with their grade, the report, the remedies with their risk.
- **Never change anything.** One adapter, a short list of verbs, Secrets never read, no "apply" anywhere.

## Install and run

Not yet. When the design is accepted and built:

```text
uv tool install k8sholmes
k8sholmes investigate pod/web-7d9f -n shop
```

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [design/changes/](design/changes/) and
[design/decisions.md](design/decisions.md). Contributors: [CONTRIBUTING.md](CONTRIBUTING.md); AI coding
tools: [AGENTS.md](AGENTS.md).

## License

Apache-2.0. Copyright 2026 Bahman Shadmehr.
