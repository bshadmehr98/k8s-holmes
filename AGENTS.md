# AGENTS.md: k8sholmes

A brief for AI coding tools (Claude Code, Codex, Cursor and others) helping with this repository.
People follow the same rules; they are in [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

`k8sholmes` (import `k8sholmes`) investigates a failure in a Kubernetes cluster: it gathers evidence
read-only through `kubectl`, runs a catalogue of code checks for the known failure patterns, asks a model
once to reason over the whole case, and writes a case file with findings, their evidence and grade, a root
cause and remedies as exact commands it never runs. A command line, a Python API and a local dashboard
share the case files. Read [design/current.md](design/current.md) before changing behaviour; its §14 lists
the guarantees the tests check. Why the design looks like this: [design/changes/](design/changes/) and
[design/decisions.md](design/decisions.md).

**Status:** design 0001 is `proposed` (2026-11-01) and waits for the owner; its open questions (the name,
whether this is the product, models, where it lives) are at the end of
[design/changes/0001-initial-design.md](design/changes/0001-initial-design.md). Nothing is built beyond
the package skeleton and the errors. **Do not implement anything the owner has not accepted.**

## Commands

```bash
uv sync --all-extras                 # install everything (dev included)
scripts/check.sh                     # all quality gates: lint, format, types, tests + coverage, build, wheel smoke test
uv run pytest                        # default suite (fast, offline, deterministic; recorded clusters)
uv run pytest -m cluster             # against a throwaway local cluster (kind or minikube); by hand
uv run k8sholmes --help              # the command line (once built)
```

## Layout

```
src/k8sholmes/
  __init__.py            the public API, explicit __all__, __version__
  errors.py              K8sHolmesError and its subclasses; every message says what to do
  (planned, design §10)  records.py, ports.py, adapters/kubectl.py, adapters/openai_compatible.py,
                         gather.py, checks/, reason.py, case.py, report.py, cli.py, dashboard.py, testing/
tests/unit|contract|integration|e2e   default suite; tests/cluster|hosted: by hand, marked
tests/fixtures/clusters/              recorded clusters the default suite replays
design/                why and how: README, current.md, changes/, decisions.md, history/
docs/  examples/       user docs and runnable examples, both executed by tests once they exist
```

## Rules

1. **Simple first** ([CONTRIBUTING.md](CONTRIBUTING.md#keep-it-simple)): the plainest code that meets the
   guarantees; complexity ≤ 10, functions ~40 lines, modules ~300 lines.
2. **Design before code:** a new need or change starts as `design/changes/NNNN-<name>.md` with status
   `proposed`; the maintainer accepts it before it is built. Small choices go into `design/decisions.md`.
3. **Read-only, provably:** `kubectl` is started only from the cluster adapter, as an argument list with a
   timeout, with the verbs of design §4.1 and no other; nothing in the package writes to a cluster; no
   remedy is ever run.
4. **Code decides first:** the checks are code with stable ids; a model is asked at most once per case,
   with the whole redacted evidence, and its reply is checked by code (every reference must exist). Never
   one call per pod or per resource. A case without a model is complete.
5. **Evidence with its grade:** every finding names the evidence ids it rests on; everything read from the
   cluster is kept as returned, with its hash and time; grades are `observed`, `derived`, `reported`,
   `inferred`.
6. **Secrets never leave:** Secret bodies are never fetched; the redactor runs before anything is written
   or sent; the kubeconfig is never copied; what a model saw is exactly the recorded request.
7. **The default suite is offline and deterministic:** recorded clusters and fakes; a real cluster only
   in `tests/cluster/` (marker `cluster`), a real model only in `tests/hosted/` (marker `hosted`).
8. **Case files** are written to a temporary name and renamed, `case.json` last; the format is versioned;
   old cases open or are refused with a message that says how to migrate.
9. Never commit secrets, kubeconfigs, recordings that were not redacted and checked, or case files from a
   real cluster.
10. **Git**: Conventional Commits, small commits; AI-written commits end with a `Co-Authored-By:` line.
    The repository is local only for now: never push, tag or publish unless the maintainer asks.
