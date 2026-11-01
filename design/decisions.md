# Decisions

Smaller choices and the owner's answers. "Owner" entries are the owner's words or choices; the others are
the designer's, open to the owner's review with the design. Code comments refer to entries by number.

## Awaiting owner review

Everything below: the design is proposed, not accepted (2026-11-01).

| Id | The question | Status |
|---|---|---|
| [D-001](#d-001-the-name-k8sholmes) | Keep the name `k8sholmes` despite HolmesGPT? | awaiting owner review |
| [D-002](#d-002-python-with-the-owners-toolchain) | Python 3.11+, uv, hatchling, ruff, pyright, as the owner's other projects? | awaiting owner review |
| [D-003](#d-003-kubectl-as-a-subprocess-not-the-kubernetes-client) | Reach the cluster through `kubectl` rather than the `kubernetes` Python client? | awaiting owner review |
| [D-004](#d-004-repository-url) | The repository URL in `pyproject.toml`? | awaiting owner review |
| [D-005](#d-005-no-gpu-lock-script) | No `scripts/gpu-lock.sh`? | awaiting owner review |
| [D-006](#d-006-the-product-was-inferred-from-the-name) | Is the inferred product right? | awaiting owner review |

## D-001: The name `k8sholmes`  (2026-11-01)

- **Question:** the folder is `K8sHolmes`; "HolmesGPT" is an existing CNCF sandbox project in the same
  space ([history/0000-research.md](history/0000-research.md) §1).
- **Options:** (a) keep `k8sholmes` (PyPI name, import and CLI all `k8sholmes`); (b) a new name before
  publishing; (c) keep the working name locally, decide before the first push.
- **Choice:** (c) for now: everything is named `k8sholmes` consistently, so a rename is one search-and-
  replace. The repository is local only.
- **Status:** awaiting owner review.

## D-002: Python with the owner's toolchain  (2026-11-01)

- **Question:** Go is the native language of the Kubernetes ecosystem and ships one binary.
- **Choice:** Python 3.11+ with uv, hatchling, ruff (line length 110, complexity 10) and pyright strict for
  `src/`, so the owner's skills, review agents, hooks and `scripts/check.sh` apply unchanged. The real
  adapter runs the user's `kubectl`, so no Kubernetes client library is needed (D-003).
- **Status:** awaiting owner review.

## D-003: `kubectl` as a subprocess, not the `kubernetes` client  (2026-11-01)

- **Question:** the `kubernetes` Python client gives typed access and no external dependency at runtime;
  `kubectl` gives exactly what the user sees, honours their kubeconfig, plugins and auth helpers, and
  makes the read-only rule checkable (a list of verbs).
- **Choice:** `kubectl`, as argument lists with a timeout and `-o json`, from one adapter (design §3, §4.1).
  A `kubernetes`-client adapter can come later as an extra behind the same port.
- **Status:** awaiting owner review.

## D-004: Repository URL  (2026-11-01)

- **Choice:** `https://github.com/bshadmehr98/k8sholmes` in `pyproject.toml` and `CODEOWNERS`
  (`@bshadmehr98`), the owner's GitHub account, until the owner says where the project lives.
- **Status:** awaiting owner review.

## D-005: No GPU lock script  (2026-11-01)

- **Question:** the owner's other repositories carry `scripts/gpu-lock.sh` for real-model tests on a shared
  GPU.
- **Choice:** not copied: nothing here uses the GPU. If a local model is used through `hone-models` for
  the hosted test, the test takes the family's lock itself (`$HONE_GPU_LOCK`).
- **Status:** awaiting owner review.

## D-006: The product was inferred from the name  (2026-11-01)

- **Question:** the owner asked for the repository's structure, not for a design. The designer wrote
  design 0001 so that the structure has content to point at, from the name and the stance of the owner's
  other projects.
- **Choice:** the design is marked `proposed` everywhere; the open questions of
  [0001](changes/0001-initial-design.md) ask the owner to confirm or redirect before anything is built.
- **Status:** awaiting owner review.
