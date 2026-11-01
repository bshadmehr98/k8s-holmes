# K8sHolmes

A self-hosted platform for investigating Kubernetes environments. Ask a question in plain words. K8sHolmes
frames it, finds out what it may read, chooses from a catalog of investigative capabilities, gathers
evidence, tests explanations, and answers with findings that cite their evidence, say what they could not
know, and respect what your team has already decided is intentional. Reports are computed from the
records. You bring the model; code does the deciding.

**Status: the design is accepted and being built; milestones 1 and 2 of 4 work.** The design is in
[design/current.md](design/current.md) (§19 says what is implemented); why it looks like this is in
[design/README.md](design/README.md) and the owner's brief,
[design/history/0001-owner-brief.md](design/history/0001-owner-brief.md).

## What it does

- **Investigates any question**, not a fixed list: which services look unused, which alerts are missing,
  what we overlook, what broke without an alert, whether the environment is isolated, the likely path of a
  failure, what changed before a problem, what a failure would affect, whether the last change helped.
- **Runs tools under a code gate.** A catalog of capabilities with clear purposes, validated inputs,
  permissions, classes (inspect, probe, change) and coverage; browse, scope, test and audit them; add your
  own through a documented extension mechanism. What may run is never decided by the model.
- **Keeps evidence with coverage.** Everything read is recorded with its source, query, time and hash, and
  says whether it observed something, observed its absence, had no data, or cannot answer. "Unused",
  "isolated" and "traced" are never claimed without the source that could know.
- **Separates observation, hypothesis and reviewed conclusion**, cites evidence for and against, states
  uncertainty and missing information, and never prints invented confidence numbers.
- **Remembers your decisions.** Accept a finding as intentional with its exact scope, explanation,
  approver, review date and reconsideration triggers; later investigations apply it precisely and never
  to anything else. Keep scoped knowledge (ownership, criticality, maintenance windows, constraints) and
  see where it influenced a conclusion.
- **Reports from records**: live summaries, final reports, incident reviews, reliability and monitoring
  reviews, comparisons between runs; every number computed; shareable, permission-controlled; Markdown and
  JSON downloads.
- **Follows through**: findings move from open to acknowledged, accepted risk, fixed and verified;
  proposals state their intended effect and verification; a verification reruns the checks and compares
  before and after.
- **Recurs quietly**: save a question with its scope and schedule; later runs report what changed, not
  what stayed the same.
- **Your model, visibly**: any OpenAI-compatible endpoint, tested for connectivity, tool calling,
  streaming and structured replies, with limits, budgets and usage shown, and no silent switching.
- **A calm interface**: graphite surfaces, ivory text, copper for actions, a geometric H mark; the
  question first, the answer next, everything else behind tabs and inspectors.

## Install and run

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Until K8sHolmes is on PyPI, run it from a clone:

```text
uv sync
uv run k8sholmes demo                 # a recorded environment and an offline investigator; no cluster, no model
uv run k8sholmes serve                # http://127.0.0.1:8800/ ; sign in as demo / demo-password
```

For your own environment: start `serve` on a fresh home, open the setup link it prints to create the first
administrator, then in Settings connect an environment (a kubeconfig context on this host; only its name is
stored), add your OpenAI-compatible model endpoint and run its capability tests, and grant roles. Real
environments need `kubectl` on the PATH; K8sHolmes only ever runs its read-only verbs. The command line
investigates too: `uv run k8sholmes investigate "Why is api restarting?" --env demo`.

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [design/changes/](design/changes/) and
[design/decisions.md](design/decisions.md). Contributors: [CONTRIBUTING.md](CONTRIBUTING.md); AI coding
tools: [AGENTS.md](AGENTS.md).

## License

Apache-2.0. Copyright 2026 Bahman Shadmehr.
