# AGENTS.md: K8sHolmes

A brief for AI coding tools (Claude Code, Codex, Cursor and others) helping with this repository.
People follow the same rules; they are in [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

K8sHolmes (package, import and command `k8sholmes`) is a self-hosted platform for investigating
Kubernetes environments. A user asks a question in plain words; an investigation frames it, discovers
what it may read, chooses capabilities from a gated catalog, gathers evidence with coverage, tests
explanations and concludes with findings that cite their evidence, state their limits and respect the
team's accepted exceptions and knowledge. Reports are computed from records; proposals are verified with
before-and-after evidence; the model is the user's own OpenAI-compatible endpoint. Read
[design/current.md](design/current.md) before changing behaviour; its §18 lists the guarantees the tests
check and its §19 says what is built. Why the design looks like this: the owner's brief in
[design/history/0001-owner-brief.md](design/history/0001-owner-brief.md),
[design/changes/](design/changes/) and [design/decisions.md](design/decisions.md).

**Status:** design 0001 is accepted (2026-11-01, from the owner's brief) and being built in milestones
M1 to M4 (the table in [design/changes/0001-initial-design.md](design/changes/0001-initial-design.md)).
Nothing is built yet beyond the package skeleton. Do not implement anything the design does not say;
a gap becomes a `D-` entry or a proposed change record.

## Commands

```bash
uv sync --all-extras                 # install everything (dev included)
scripts/check.sh                     # all quality gates: lint, format, types, tests + coverage, build, wheel smoke test
uv run pytest                        # default suite (fast, offline, deterministic; recordings and fakes)
uv run pytest -m cluster             # against a throwaway local cluster (kind or minikube); by hand
uv run k8sholmes demo                # the demo environment and scripted model in the web interface (once built)
uv run k8sholmes serve               # the web interface at http://127.0.0.1:8800/ (once built)
```

## Layout

The module boundaries of design §3; each module names the sections it implements.

```
src/k8sholmes/
  __init__.py  errors.py           the public API with an explicit __all__; K8sHolmesError and its subclasses
  records.py  store.py  auth.py    every record (pydantic); SQLite with a versioned schema; users, roles, sessions, audit
  environments.py  cluster.py      environments, permissions, inventory and relationships; the Cluster port and the kubectl adapter
  capabilities/                    the capability model, registry and gate; the built-in Kubernetes, knowledge and probe capabilities
  engine.py  prompts/              the investigation engine in five phases; the system prompts and output schemas
  evidence.py  findings.py         evidence with coverage and redaction; findings, conditions, states, revisions
  knowledge.py                     knowledge notes and exceptions: matching, expiry, revocation, influence
  reports.py  proposals.py  saved.py   reports from records; proposals and verification; saved and recurring investigations
  models.py                        the Model port, the OpenAI-compatible client, capability tests, usage
  server.py  api.py  page/         the standard-library web server, the JSON API and events, the static interface
  cli.py  testing/                 the command line; FakeCluster, RecordedCluster, ScriptedModel, the demo, contract checkers
tests/unit|contract|integration|e2e   default suite; tests/cluster|hosted: by hand, marked
tests/fixtures/clusters/              recorded clusters the default suite and the demo replay
design/                why and how: README, current.md, changes/, decisions.md, history/
docs/  examples/       user docs and runnable examples, both executed by tests once they exist
```

## Rules

1. **Simple first** ([CONTRIBUTING.md](CONTRIBUTING.md#keep-it-simple)): the plainest code that meets the
   guarantees; complexity ≤ 10, functions ~40 lines, modules ~300 lines; the module boundaries of design
   §3, no services, no frameworks.
2. **Design before code:** a new need or change starts as `design/changes/NNNN-<name>.md` with status
   `proposed`; the maintainer accepts it before it is built. Small choices go into `design/decisions.md`.
3. **The gate is code:** what may run is decided by the registry from the user's role and the
   environment's settings (design §5.2), never by the model and never by text in a result; `kubectl` is
   started only from the cluster adapter, as an argument list with a timeout, with the read-only verbs of
   §5.3; no capability writes to a cluster in 0.1; nothing applies a proposal.
4. **Coverage before conclusions:** every result states `observed`, `observed_absent`, `no_data` or
   `cannot_answer`; the engine's validation turns unsupported conclusions into limitations (§6.3, §7.2);
   "unused", "isolated" and "traced" are never said without the source that could know.
5. **Evidence, not opinion:** every finding cites evidence that exists; findings are `observation` or
   `hypothesis` until a person marks a `conclusion`; no confidence numbers; every number in a report is
   computed from records; knowledge notes are shown as influence, never cited as evidence.
6. **Secrets and access:** the redactor runs before storage and before every model message; Secret bodies
   are never fetched; keys live in the secrets file or the environment, never in the database, a message,
   a report, an export or a log; every API route checks the session, the role and the environment; the
   audit log records what §14.4 lists.
7. **Exceptions match exactly** (environment, condition, every resource) and never suppress anything
   else; every edit is a revision; expired and revoked exceptions are named, not applied.
8. **Persist every step:** steps, calls and evidence are committed as they complete; an interrupted
   investigation resumes; only retryable model failures are retried, on the same endpoint; capability calls
   are never retried by the engine.
9. **The default suite is offline and deterministic:** recordings, `FakeCluster`, `ScriptedModel`, a
   temporary store; a real cluster only in `tests/cluster/` (marker `cluster`), a real model only in
   `tests/hosted/` (marker `hosted`). Never commit kubeconfigs, unredacted recordings or real
   investigations.
10. **Git**: Conventional Commits, small commits; AI-written commits end with a `Co-Authored-By:` line.
    The repository is local only for now: never push, tag or publish unless the maintainer asks.
