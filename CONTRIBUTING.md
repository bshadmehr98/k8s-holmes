# Contributing to k8sholmes

Thank you for helping. This page covers how to set up, the rules the code follows, how it is tested and
how the design changes. The design itself is in [`design/`](design/README.md); start with
[`design/current.md`](design/current.md). The first design, [0001](design/changes/0001-initial-design.md),
is proposed and waits for the maintainer.

## Setup and the quality gates

```bash
uv sync --all-extras          # install the package with every extra and the dev tools
scripts/check.sh              # every quality gate; "green" means this exits 0
```

The real cluster adapter needs `kubectl` on the `PATH`; the default test suite does not.

`scripts/check.sh` runs ruff (lint and format), pyright (strict for `src/`), the default test suite with
branch coverage of at least 90% on `src/`, `uv build`, and a smoke test that installs the built wheel
alone in a fresh virtual environment (and runs the README quickstart once there is one). Pull requests
must keep it green. Never weaken a test to make it pass.

Other useful commands:

```bash
uv run pytest                          # default suite: fast, offline, deterministic
uv run pytest tests/e2e                # the acceptance cases only
uv run ruff check . && uv run ruff format .
uv run pyright
uv run pytest -m cluster               # a throwaway local cluster (see below)
```

## Keep it simple

The first rule: **simple, easy to understand, maintainable and expandable.** When simplicity conflicts
with anything except correctness and the guarantees in [`design/current.md`](design/current.md#14-guarantees-acceptance-cases),
simplicity wins. A newcomer should be able to open any module, understand what it does in a few minutes
and change it safely.

1. Build the simplest thing that passes the acceptance cases, not the most general thing.
2. No speculative generality. Add an abstraction only when there are two real uses today, or the design
   names it as an extension point (a port, the check table).
3. Extend through a few clear seams named in the design: the `Cluster` and `Reasoner` ports and the check
   catalogue. Everything else is ordinary concrete code.
4. Plain Python first: functions and small dataclasses before classes, composition before inheritance,
   the standard library before dependencies.
5. Flat and explicit: short call chains, no metaclasses, import hooks, monkey-patching or deep decorator
   stacks.
6. One obvious way to configure and call each thing.
7. Names over comments; comments say *why*.
8. Delete freely: dead code, unused parameters and single-caller helper layers go.

Limits, checked in review (ruff enforces complexity):

| Thing | Limit |
|---|---|
| Cyclomatic complexity per function | 10 (`C901`) |
| Function length | about 40 lines |
| Module length | about 300 lines |
| Parameters per function | 6; group more into a small dataclass |
| Inheritance depth of own classes | 1 |
| Variants | a dict of functions or a `match`, not a class hierarchy (checks are functions in a table) |
| Core dependencies | the standard library and pydantic; anything else is an optional extra, and a new one needs a written reason in `design/decisions.md` |

Avoid "manager / handler / processor" layers, base classes with one subclass, plugin systems beyond
entry points, event buses and dependency-injection containers.

## Code conventions

- Python 3.11 or newer; ruff with line length 110; pyright strict for `src/`; the public API is fully
  typed and `py.typed` is shipped.
- The public API is exported from `src/k8sholmes/__init__.py` with an explicit `__all__`; public
  functions and classes have docstrings.
- **The core is useful alone.** It never imports an optional extra at import time; adapters that need one
  are imported lazily. `tests/unit/test_import_boundaries.py` checks this.
- **Ports and adapters.** A port is a small `typing.Protocol` owned by k8sholmes; adapters are injected
  as constructor arguments; every port has a fake and a contract checker in `k8sholmes.testing`.
- **The cluster through one module, read-only.** Only the `kubectl` adapter starts `kubectl`: argument
  lists (never a shell), `-o json`, a timeout on every call, `--context` and `--namespace` explicit, and
  only the verbs of [`design/current.md`](design/current.md) §4.1. It never fetches a Secret's body.
- **Secrets.** The redactor runs before anything is written to a case or sent to a model; the kubeconfig
  is never copied; nothing secret reaches records, reports or logs.
- **Explicit failure.** Never swallow errors silently. Errors crossing the public API are typed
  (`K8sHolmesError` and its subclasses in `errors.py`), and messages say what happened and what to do. A
  failed read is kept as evidence of kind `error`, never dropped.
- **Case files.** Written to a temporary name and renamed into place, `case.json` last; the format is
  versioned (`format_version`). Adding optional keys keeps the version; removing a key or changing its
  meaning needs a new version and a design change.
- **Determinism.** Ids come from `hashlib`, never the built-in `hash()`; ordering is explicit.
- Time is `datetime.now(UTC)`, stored as ISO-8601 UTC; durations are integer milliseconds; paths are
  `pathlib.Path`; logging goes to `logging.getLogger("k8sholmes")`, never `print` in library code.
- Secrets (a model API key) come only from environment variables.

## Tests

| Suite | Folder | Runs by default | Purpose |
|---|---|---|---|
| Unit | `tests/unit/` | yes | each module's behaviour, edge cases and errors, with `FakeCluster` |
| Contract | `tests/contract/` | yes | every `Cluster` and `Reasoner` adapter and fake passes the contract checkers |
| Integration | `tests/integration/` | yes | modules together with real local resources: the filesystem, subprocesses, a fake `kubectl` on the PATH |
| Acceptance | `tests/e2e/` | yes | the numbered guarantees of [`design/current.md`](design/current.md#14-guarantees-acceptance-cases), through the public API and CLI only, on recorded clusters |
| Cluster | `tests/cluster/` | no | a throwaway local cluster with manifests that reproduce each failure pattern, marker `cluster` |
| Hosted | `tests/hosted/` | no | one real model call, marker `hosted`; needs the model key in the environment |

- The default suite must stay fast, deterministic and offline. It never runs `kubectl`: tests replay
  recordings from `tests/fixtures/clusters/` or build resources in `FakeCluster`.
- Every acceptance case has a test in `tests/e2e/` named `test_ac<N>_<slug>`, using only the public API or
  CLI. An acceptance test must fail if its feature is removed.
- Every check has a recording that makes it fire and one that does not.
- The README quickstart, every Python block in `docs/` and every file in `examples/` are run by the tests,
  once they exist. Each example opens with a What / How / Why docstring and is listed in
  `examples/README.md`.
- What is stored is tested by reopening it; crash safety with a subprocess killed mid-write.
- A planted fake token in a recording must appear nowhere in the case file, the report or the logs.

### Cluster tests

The cluster tests (`-m cluster`, in `tests/cluster/`) apply the manifests in `tests/cluster/manifests/`
to a fresh namespace of a **throwaway** local cluster (kind or minikube), investigate, and delete the
namespace. `KUBECONFIG` and `K8SHOLMES_TEST_CONTEXT` choose the cluster; a test refuses a context whose
name does not contain `kind-` or `minikube` unless `K8SHOLMES_TEST_ALLOW_CONTEXT=1`, because the
manifests break things on purpose. They skip with a reason when `kubectl` or the cluster is missing. A
check's evidence gathering is proven on the real cluster once and then recorded for the default suite
(the `real-cluster-tests` skill in `.claude/skills/` says how).

## Changing the design

Design changes are written down before they are built:

1. Write `design/changes/NNNN-<short-name>.md` with status `proposed` and the sections Status, Context,
   Problem, Options, Decision, Consequences, and Migration and compatibility.
2. The maintainer reviews it; the status becomes `accepted` (or `rejected`).
3. Implement it from [`design/current.md`](design/current.md) and the accepted change records, tests
   first.
4. Update `design/current.md`, set the record to `implemented in <version>`, and add a `CHANGELOG.md`
   entry that links to it.

Smaller implementation choices that need no change record go into
[`design/decisions.md`](design/decisions.md).

## Branches and pull requests

- One branch per change, named `<type>/<short-name>` after the Conventional Commit types
  (`feat/pod-checks`).
- While the repository is local only, the maintainer merges finished branches into `main`.
- Once published: fill in [`.github/pull_request_template.md`](.github/pull_request_template.md); claude[bot]
  reviews every pull request ([`.github/workflows/claude-review.yml`](.github/workflows/claude-review.yml));
  answer each thread; `main` accepts changes only through pull requests with CI green and every review
  thread resolved; the maintainer merges.

## Commits

- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`), a
  subject of at most 72 characters, a body that explains why; one logical change per commit.
- Commits written with an AI tool end with a `Co-Authored-By:` line naming it.
- Never commit secrets, kubeconfigs, unredacted recordings or case files from a real cluster.
- `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Contributors using AI coding tools will find a short brief for them in [`AGENTS.md`](AGENTS.md); Claude
Code users also get the whole workflow as skills, reviewer agents and hooks in [`.claude/`](.claude/).
