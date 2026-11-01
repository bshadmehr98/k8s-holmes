# 0001: Initial design: an infrastructure investigation platform (v0.1)

## Status

`accepted 2026-11-01 by the owner` (the brief, [history/0001-owner-brief.md](../history/0001-owner-brief.md):
"Build **K8sHolmes**, an open-source, self-hosted infrastructure investigation platform implemented in
**Python**"). The first version of this record, written the same morning from the project's name alone,
proposed a smaller command-line investigator; the owner's brief replaced it, and this record was rewritten
from the brief. The designer's choices inside the brief's freedom (the web server, the store, the model
client, how the cluster is reached) are in [decisions.md](../decisions.md) and open to the owner's review;
they do not block the build.

## Context

On 2026-11-01 the owner set up this repository in the shape of their other projects and then gave the
brief: a self-hosted platform, in Python, that helps engineers understand their Kubernetes environments,
investigate problems, discover blind spots and improve reliability, through conversational investigation,
controlled tool execution, persistent organizational knowledge and evidence-backed reports in a polished
web interface. The brief prescribes behaviour in detail (fifteen sections) and no framework, database or
supporting product.

Two open-source tools are near this space ([history/0000-research.md](../history/0000-research.md)):
HolmesGPT, an agentic troubleshooter and CNCF sandbox project, and k8sgpt, code analyzers with a model's
explanation. Neither keeps organizational knowledge and accepted exceptions, grades evidence coverage,
separates observation from hypothesis from reviewed conclusion, or verifies proposals against before and
after evidence. Those are the brief's centre.

The owner's other projects share a stance this design keeps: code decides first and a model reasons over
what code gathered; every claim carries its evidence and grade; nothing is changed on the user's behalf
without an explicit path; records are the product; the interface is calm.

## Problem

The brief's own words (§1): reduce "the effort required to gather context, investigate a question,
explain a conclusion, and prevent repeated problems". Today that effort is a ritual of `kubectl` commands
in the right order, knowledge in people's heads and chat threads, findings repeated every time someone
looks, AI helpers that guess from pasted output or drive the cluster unaudited, and reports whose numbers
nobody can check.

## Options

1. **Leave it as it is:** `kubectl`, dashboards, chat windows and a wiki. Nothing connects them.
2. **An agent with a shell:** a model that runs `kubectl` freely. Flexible and quick to build; unauditable,
   unsafe without a gate, no knowledge, no coverage, no verification. HolmesGPT exists.
3. **Analyzers with explanations:** a fixed catalogue of checks (k8sgpt's shape) with a model explaining
   each. Deterministic, but every question needs its own analyzer, which the brief rules out (§2).
4. **A general investigation engine over a gated capability catalog, with evidence, knowledge, exceptions
   and reports as first-class records** (chosen): the model chooses capabilities and reasons; code
   validates inputs, enforces permissions, records evidence with coverage, matches exceptions, computes
   every number and builds every report; the web interface shows the little that matters first.

## Decision

Option 4, as described in [design/current.md](../current.md): the concepts (§2), one package with module
boundaries (§3), environments with an inventory and relationships carrying their basis (§4), the
capability catalog with classes, a code gate, sixteen built-in capabilities and two extension mechanisms
(§5), the engine in five phases with validation and control (§6), evidence with coverage rules (§7),
findings with kinds, states and revisions (§8), exceptions and knowledge (§9), reports computed from
records (§10), proposals, verification and the authorization path (§11), saved and recurring
investigations (§12), bring-your-own-model with capability tests and comparisons (§13), access control,
privacy and audit (§14), the interface, brand tokens and command line (§15), persistence and reliability
(§16), the test suites and the demo (§17), and 24 guarantees (§18).

Designer's choices within the brief: a standard-library web server with a static page (D-007), SQLite
(D-008), the model client over `urllib` (D-009), the cluster through `kubectl` (D-003, D-010), local
accounts (D-011), a recorded demo environment with a scripted model (D-012).

### Milestones

| Milestone | Delivers | Guarantees |
|---|---|---|
| M1: the engine on records | `records.py`, `store.py` (schema v1), `auth.py` (roles, sessions, audit), the `Cluster` port with `Kubectl`, `FakeCluster` and `RecordedCluster`, the capability model, registry and gate, the built-in capabilities, evidence and the redactor, the `Model` port with the OpenAI-compatible client and `ScriptedModel`, the engine (five phases, validation, cancel, resume, follow-up), findings, exceptions and knowledge matching, the CLI `investigate`, `capabilities`, `demo` (headless), the demo recording | 1–10, 15, 19, 20, 22, 23 |
| M2: the interface | `server.py`, `api.py`, server-sent events, `page/` with the tokens and the mark: Investigate, Investigations and the investigation page, Capabilities (catalog, settings, test, history), Knowledge (notes, exceptions), Settings (environments, models with capability tests, users, retention, audit); `setup`, `serve` | 14, 16, 17, 21 |
| M3: reports and follow-through | `reports.py` (five kinds, revisions, edits, shares, exports), `proposals.py` (proposals, verification, the authorization path), `saved.py` (saved investigations, schedules, comparison, notifications), retention purge, the model comparison | 11, 12, 13, 18, 24 |
| M4: real clusters and documentation | `tests/cluster/` manifests for the demo scenarios, the hosted model test, `docs/` (setup, configuration, capabilities, extensions, demo, privacy), README quickstart, examples | all, on a real cluster |

Each milestone ends with `scripts/check.sh` green, its guarantees passing, `design/current.md` §19 updated
and the docs in step. The owner is shown a demo after each milestone.

## Consequences

- An engineer gets a recorded, evidence-backed investigation from a question, with the team's knowledge
  applied and every number checkable; a model does the connecting, code does the deciding.
- The capability catalog and the coverage rules are the trust boundary; adding a source later means
  adding capabilities, not rewriting the engine.
- Core dependencies stay at the standard library plus pydantic; the interface is plain HTML, CSS and
  JavaScript without a build step; one SQLite file; `kubectl` at runtime for real environments.
- Not applying changes keeps 0.1 safe and smaller; teams that want "fix it" get the proposal, the
  authorization path and the verification, and run the change themselves.
- The name is still open (D-001): HolmesGPT exists in this space.

## Migration and compatibility

A new product; nothing to migrate. The store carries `schema_version` from the first release and refuses
a newer schema with a message; the export format (`report.json`) carries `format_version "1"`; capability
ids and condition codes are stable from the first catalogue; the public Python API is `k8sholmes` with an
explicit `__all__`.

## Open questions for the owner

Answered by the brief: what the product is (D-006), Python (D-002), bring-your-own OpenAI-compatible
models (so `hone-models` is not a dependency, D-013). Still open, none blocking the build:

1. **The name** (D-001): keep `k8sholmes` or choose another before publishing.
2. **Where it lives** (D-004): the owner's account, `honeworks`, or private for now.
3. **The one probe** (`http.probe_url`): ship it off by default in 0.1, or leave probes to a later change?
