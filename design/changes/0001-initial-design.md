# 0001: Initial design: a detective for Kubernetes clusters (v0.1)

## Status

`proposed` (2026-11-01). Written by the designer from the project's name and the owner's request to set the
repository up in the shape of their other projects; the owner has not described the product. The open
questions at the end decide what is built, and [decisions.md](../decisions.md) lists the choices made
meanwhile, all awaiting the owner's review. **Nothing is implemented until the owner accepts this record.**

## Context

On 2026-11-01 the owner created an empty folder named `K8sHolmes` and asked for it to be set up with the
same structure as their other repositories: git, the design folder, the changelog, the skills, the review
agents and the GitHub workflows, "everything". The name says the rest: *K8s* for Kubernetes, *Holmes* for
an investigator who works from evidence. A survey of what exists ([history/0000-research.md](../history/0000-research.md))
found two open-source tools in this space, HolmesGPT (an agentic troubleshooter, CNCF sandbox) and k8sgpt
(code analyzers with an LLM explanation), and a naming clash with the first.

The owner's other projects share a stance this design adopts: code decides first, a model is asked few and
whole questions and its reply is checked, every claim carries evidence and its grade, nothing is changed on
the user's behalf, and the record of a run is the product. Their machine has `kubectl` 1.36 and Docker,
one kubeconfig context, and no local cluster tool yet.

## Problem

Debugging a Kubernetes failure is a known ritual done by hand, in the right order, every time; the common
causes have signatures in the cluster's own records that nothing checks together; the evidence is lost in
a terminal; AI helpers either guess from a pasted `describe` or drive `kubectl` themselves, slowly and
unauditably; and Secrets leak into the tooling along the way. The full statement is in
[README.md](../README.md).

## Options

1. **Leave it as it is:** `kubectl` by hand, and a chat window. Nothing recorded, the same ritual every
   time.
2. **An agent that drives `kubectl`** (the HolmesGPT shape): the model chooses what to read next. Flexible,
   but slow and expensive per investigation, hard to audit, nondeterministic, and one bad tool call away
   from a write. The space already has a CNCF project doing it.
3. **Code gathers and checks, one model call reasons, a case file records** (chosen): a short list of
   read-only verbs behind one adapter; a walk from the symptom outward, driven by what the checks need; a
   catalogue of checks with stable ids; one structured whole-case reasoning call whose references are
   checked against the evidence; a case folder that opens later; remedies as commands, never run.
4. **Contribute checks to k8sgpt instead.** Cheapest for the world, but k8sgpt is Go, explains per finding
   and keeps no case file; the owner's reasons for their own project (a Python family, records as the
   product, their own process) are not met.

## Decision

Option 3, as described in [design/current.md](../current.md): the concepts and grades (§2), the `Cluster`
port with the `kubectl` adapter, recordings and fakes (§3), the allowed verbs and the walk (§4), the first
catalogue of 26 checks (§5), the `Reasoner` port with one checked call (§6), the case file (§7), the
report and remedies (§8), the CLI (§9), the Python API (§10), the dashboard (§11), the Secret rules (§12),
and 14 guarantees (§14).

### Milestones

| Milestone | Delivers | Guarantees |
|---|---|---|
| M1: records and the cluster port | `records.py`, `ports.py`, errors, `Kubectl` with recording, `RecordedCluster`, `FakeCluster`, the contract checker, the redactor, the case folder written and reopened, the CLI skeleton | 1, 7, 8, 9, 12 |
| M2: the walk and the pod checks | `gather.py` with the budget, the pod and node checks of §5 with their recordings, the report | 2 (pod and node checks), 3, 4, 10, 13 |
| M3: the workload, service and storage checks | deployment, service, ingress, pvc, job, cronjob, rbac and hpa checks with their recordings; the namespace symptom | 2 (the rest) |
| M4: reasoning | `Reasoner`, `OpenAICompatible`, `FakeReasoner`, the request digest, the reply checks and repairs, the recording of calls; the `hone-models` extra | 5, 6 |
| M5: the command line and the dashboard | every command of §9, `--json`, exit codes; the dashboard of §11 | 11, 14 |
| M6: real clusters | the kind manifests for every check, the cluster suite, the hosted test, README, docs and examples | all, on a real cluster |

Each milestone ends with `scripts/check.sh` green, its acceptance cases passing and the docs in step.

## Consequences

- A failure gets a first, deterministic answer in seconds, a recorded investigation, and remedies the
  user can read before running; a model adds a narrative only over evidence that was actually read.
- Core dependencies stay at the standard library plus pydantic; `kubectl` is a runtime requirement for the
  real adapter; `hone-models` is an optional extra.
- The check catalogue is the maintenance burden: each check needs two recordings and a manifest for the
  real-cluster test. That is also what makes it trustworthy.
- The name may have to change (open question 1); everything in the repository that carries the name is
  a search-and-replace away.
- Writes to the cluster are not available even as an option, by design; users who want "fix it" push a
  button elsewhere.

## Migration and compatibility

A new package; nothing to migrate. The case file carries `format_version "1"` from the first release;
check ids are stable from the first catalogue; the public API is `k8sholmes` with an explicit `__all__`.

## Open questions for the owner

1. **The name.** HolmesGPT exists (CNCF sandbox, PyPI `holmesgpt`). Keep `k8sholmes` and accept the
   confusion, or choose another name before anything is published? ([D-001](../decisions.md))
2. **Is this the product?** The design was inferred from the name. If the owner had something else in
   mind (a Kubernetes dashboard, a cluster-aware chat, something to run inside the cluster, something for
   their own hone family's workloads), this record should be rewritten before acceptance.
3. **Models.** Reach models through `hone-models` (the owner's family, local and hosted) as the core path,
   or through the plain OpenAI-compatible adapter with `hone-models` as an extra, as proposed? Which
   model by default?
4. **Where it lives.** A public repository under the owner's account, under `honeworks`, or private for
   now? The URLs in `pyproject.toml` point at `github.com/bshadmehr98/k8sholmes` meanwhile ([D-004](../decisions.md)).
5. **The dashboard in 0.1,** or CLI and Python API first and the dashboard as change 0002?
6. **Secret names.** Checking whether a referenced Secret exists needs `get secret <name> -o name`. The
   design hides that behind a flag the user turns on. Is reading Secret *names* acceptable by default?
7. **Language.** Python with the owner's toolchain (uv, ruff, pyright) is assumed, as in every other
   project of theirs; Go would fit the Kubernetes ecosystem better and ship a single binary. Python?
