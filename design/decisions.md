# Decisions

Smaller choices and the owner's answers. "Owner" entries are the owner's words or choices; the others are
the designer's, open to the owner's review. Code comments refer to entries by number.

## Awaiting owner review

The design is accepted (2026-11-01, the brief). These designer's choices do not block the build and may be
revised by the owner at any time:

| Id | The question | Status |
|---|---|---|
| [D-001](#d-001-the-name-k8sholmes) | Keep the name `k8sholmes` despite HolmesGPT? | awaiting owner review |
| [D-004](#d-004-repository-url) | The repository URL in `pyproject.toml`? | awaiting owner review |
| [D-007](#d-007-a-standard-library-web-server-and-a-static-page) | A standard-library web server and a static page, no framework? | awaiting owner review |
| [D-008](#d-008-sqlite) | SQLite as the store? | awaiting owner review |
| [D-009](#d-009-the-model-client-over-urllib) | The model client over `urllib`? | awaiting owner review |
| [D-011](#d-011-local-accounts) | Local accounts with roles per environment? | awaiting owner review |
| [D-014](#d-014-one-probe-off-by-default) | One probe capability, off by default? | awaiting owner review |

## The owner's answers (2026-11-01)

| Id | Decision | Who |
|---|---|---|
| D-006 | The product is the one in the brief: a self-hosted infrastructure investigation platform, not the command-line investigator first inferred from the name. The first design record was rewritten from the brief. | owner (the brief) |
| D-002 | Python is required; no framework, database or supporting product is prescribed. The owner's toolchain (uv, hatchling, ruff with line length 110 and complexity 10, pyright strict for `src/`) is used so the skills, agents, hooks and `scripts/check.sh` apply unchanged. | owner (the brief), designer (toolchain) |
| D-013 | Models are the user's own OpenAI-compatible endpoints (brief §9). `hone-models` is therefore not a dependency; a user may point K8sHolmes at any compatible gateway. | owner (the brief) |

## D-001: The name `k8sholmes`  (2026-11-01)

- **Question:** the folder is `K8sHolmes`; "HolmesGPT" is an existing CNCF sandbox project in the same
  space ([history/0000-research.md](history/0000-research.md) §1). The brief says "Use **K8sHolmes**
  consistently throughout the product".
- **Choice:** K8sHolmes in the product and the docs; `k8sholmes` as the package, import and command.
  Everything carries the name consistently, so a rename is one search-and-replace before publishing.
- **Status:** awaiting owner review.

## D-003: `kubectl` as a subprocess, not the `kubernetes` client  (2026-11-01)

- **Question:** the `kubernetes` Python client gives typed access with no external program; `kubectl`
  gives exactly what the user sees, honours their kubeconfig, plugins and auth helpers, and makes the
  read-only rule checkable (a list of verbs).
- **Choice:** `kubectl`, as argument lists with a timeout and `-o json`, from one adapter (design §5.3).
  A `kubernetes`-client adapter can come later behind the same `Cluster` port.
- **Status:** designer's choice; confirmed workable by the brief's silence on it.

## D-004: Repository URL  (2026-11-01)

- **Choice:** `https://github.com/bshadmehr98/k8sholmes` in `pyproject.toml` and `@bshadmehr98` in
  `CODEOWNERS` until the owner says where the project lives.
- **Status:** awaiting owner review.

## D-005: No GPU lock script  (2026-11-01)

- **Choice:** `scripts/gpu-lock.sh` from the owner's other repositories is not copied: nothing here uses
  the GPU. A hosted model test that uses a local model takes the family's lock itself if needed.
- **Status:** designer's choice.

## D-007: A standard-library web server and a static page  (2026-11-01)

- **Question:** the brief wants a polished web interface with live updates and prescribes no framework.
  The owner's other dashboards are `http.server` plus a static page with no build step.
- **Options:** (a) FastAPI with uvicorn and a bundled front end; (b) the standard library:
  `ThreadingHTTPServer`, a JSON API, server-sent events, static HTML, CSS and JavaScript modules.
- **Choice:** (b). It keeps the core at the standard library plus pydantic, matches the owner's other
  products, needs no build step, and a few concurrent users is the stated scale (design §20). If the
  product outgrows it, the API module is the seam.
- **Status:** awaiting owner review.

## D-008: SQLite  (2026-11-01)

- **Choice:** one SQLite file in WAL mode with a versioned schema (design §16.1): self-hosted, zero
  administration, transactional, in the standard library. Evidence bodies are capped text in the same
  file, so an export is one file plus a folder.
- **Status:** awaiting owner review.

## D-009: The model client over `urllib`  (2026-11-01)

- **Question:** `httpx` or the OpenAI SDK would be shorter to write; the brief asks for OpenAI-compatible
  endpoints including private deployments, tool calling, streaming and structured replies.
- **Choice:** `urllib` with hand-parsed server-sent events, one module (design §13.1). No dependency,
  every byte sent is visible, and private gateways that deviate from the SDK's assumptions still work.
- **Status:** awaiting owner review.

## D-010: Read-only verbs and no change capability in 0.1  (2026-11-01)

- **Choice:** the `Kubectl` adapter knows `get`, `list`, `logs`, `top`, `auth can-i`, `version` and
  `api-resources` and refuses everything else and every Secret body; the `change` class, its gate and the
  authorization path exist (design §5.2, §11.3) and no change capability ships. Verification reruns
  inspect capabilities after the user applied the change.
- **Status:** designer's choice, within the brief ("Infrastructure changes must require an explicitly
  configured authorization path").

## D-011: Local accounts  (2026-11-01)

- **Choice:** usernames and `scrypt` password hashes, HTTP-only session cookies, roles per environment
  (design §14.1), a one-time setup link for the first admin. Single sign-on is a later change behind the
  same `auth.py` seam.
- **Status:** awaiting owner review.

## D-012: A recorded demo environment and a scripted model  (2026-11-01)

- **Choice:** the demo (design §17.2) replays a recorded cluster and a scripted model, so the end-to-end
  experience of the brief's §15 runs offline, deterministically, and is tested as guarantee 22. The demo
  is also the fixture of most acceptance tests.
- **Status:** designer's choice.

## D-014: One probe, off by default  (2026-11-01)

- **Question:** the brief separates passive inspection, active probes and changes. Probes from inside the
  cluster need `exec` or port-forwarding; the only probe that needs neither is an HTTP request from the
  K8sHolmes host.
- **Choice:** `http.probe_url` ships as the one `probe`, disabled until an operator enables it per
  environment, so the probe class and its gate are real and tested.
- **Status:** awaiting owner review (0001 open question 3).
