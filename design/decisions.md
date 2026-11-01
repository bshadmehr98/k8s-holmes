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
| [D-021](#d-021-choices-of-milestone-3) | The choices of milestone 3: short-lived evidence, verification by code, the scheduler, shares, model comparison? | awaiting owner review |
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

## D-015: The demo user is an operator, not an administrator  (2026-11-01)

- **Question:** `k8sholmes demo` creates the account `demo` with a fixed password; the first version made it
  an administrator, a well-known admin login on every installation that ran the demo (review of M1).
- **Choice:** the demo user is an `operator` on the demo environment only. The first administrator is made
  through the one-time setup link (design §14.1); the demo never creates one.
- **Status:** designer's choice from the review.

## D-016: A recording is a snapshot, not request and reply pairs  (2026-11-01)

- **Question:** design §3 of the first record described recordings as every `kubectl` request and reply.
- **Choice:** a recording folder holds the resources by kind, the events, the logs, the `top` rows, the
  `can-i` answers, the version and the kinds (`FakeCluster.save`, `Kubectl(record=…)`); `RecordedCluster`
  answers any query from it with label and field selectors. Simpler to read and to author (the demo is
  built in code), and a test's query need not match a recorded request byte for byte.
- **Status:** designer's choice.

## D-017: What the model sees of a large result  (2026-11-01)

- **Question:** a tool result such as the inventory can be hundreds of kilobytes; the model gets a message
  of bounded size.
- **Choice:** `thread.shrink` keeps a result's shape and replaces its largest values first (`items` of an
  inventory go before `relationships` and `counts`), noting what was omitted; the full record is evidence.
- **Status:** designer's choice.

## D-018: Every failure inside a capability is a recorded call  (2026-11-01)

- **Question:** the capability boundary saw only typed errors; an unexpected exception (a `KeyError` on odd
  cluster JSON) escaped and left the investigation `running` (review of M1).
- **Choice:** `Registry.execute` catches every exception from a capability's `run` and records a failed
  call with the error named; the engine marks an investigation `failed` on any other exception and re-raises
  it, so no row stays `running` without a process behind it.
- **Status:** designer's choice from the review.


## D-019: The shape of the runner  (2026-11-01)

- **Question:** the interface needs investigations to run in the background and their events to reach a
  streaming response; CONTRIBUTING lists event buses among the things to avoid.
- **Choice:** one `Runner` class (design §3): a thread per running investigation, a dict of subscriber
  queues per investigation that the engine's `on_event` hook feeds, idempotency keys scoped to the user and
  the environment and forgotten when the run ends, and a safety net that marks a run `failed` if anything
  escaped the engine. No separate bus object, no thread pool, no queue of pending runs: a few concurrent
  users is the stated scale (§20).
- **Status:** designer's choice from the review of M2.

## D-020: Access and limits of the interface  (2026-11-01)

- **Choice:** sessions last 7 days (`HttpOnly; SameSite=Strict`); the event stream sends a heartbeat every
  15 s; request bodies are capped at 4 MiB; capability settings per environment are administered (§14.1
  lists capabilities under `admin`), so the Capabilities page shows the settings form to administrators only;
  the first administrator's token lives in the home folder until used (`auth.setup_token`).
- **Status:** designer's choice from the review of M2.

## D-021: Choices of milestone 3  (2026-11-01)

- **Choice:** (a) the evidence of `k8s.logs` and `k8s.top` is `short` (raw, bulky, time-bound); every other
  capability's evidence lives with its investigation; a finding that leaves `open` pins the evidence it
  cites. (b) A verification is judged by code for the known conditions (`pod.crashloop.oom`,
  `replicas.single`, `probe.missing`, `limits.missing`, `pdb.missing`, `service.no-backends`,
  `alert.missing`); for any other condition the outcome is `not_verified` with "compare by hand", never
  `verified`; a result that failed or did not observe (coverage other than `observed`, or no pods) says
  nothing, and a proposal's `wait_ms` is honoured by refusing a verification before the wait after
  `applied` has passed. (c) The comparison of two runs hashes each piece of evidence without its bookkeeping keys
  (`inventory_id`, `period_start`, `period_end`, `taken_at`), so a moving window is not "changed evidence".
  (d) The scheduler ticks every 60 s in the server, runs a due saved investigation as the person who saved it,
  moves `next_run_at` before starting (a failing start never loops) and purges once at its first tick and
  then daily; schedules run only while the server runs (§20). (e) A share token is random, shown once,
  stored as a SHA-256 hash, lasts 1 to 90 days (default 7), opens one report and nothing else of the API.
  Shares are the administrator's, as §14.1 says. (f) Proposal states by role: investigators propose,
  reject and reopen; operators approve, mark applied and verify; `verified` is only ever set by a
  verification, which moves the findings it covers to `verified` from any state but `accepted_risk`.
  (g) The model comparison of §13.4 is the fixed-evidence one, on a finished investigation only, recording
  its model calls under the phase `comparison` and touching nothing else of the investigation; a free
  comparison is an investigation started with another endpoint, labelled by its own model name, with no
  separate feature. (h) A saved investigation's period rule becomes the run's window when it reads "the
  last N hours/days"; any other rule is passed to the model as words. (i) The follow-through after a saved
  run (compare, store the report, notify) is one function, `saved.finish_run`, called by the server for
  runs only, never for follow-ups.
- **Why:** each keeps the guarantee (24, 18, 13, 21) with the least machinery; (b) in particular refuses
  to let a model or a heuristic declare a fix verified.
- **Status:** designer's choice from milestone 3; awaiting owner review.

## D-022: A kind the cluster does not serve is not a refused permission  (2026-11-01)

- **Choice:** connecting leaves out of the permission matrix every checked kind the cluster does not serve
  (a CRD that is not installed, such as `prometheusrules` or `servicemonitors`), instead of recording it
  as `false`. The gate refuses only on `false`, so the capability runs and states `cannot_answer` itself.
- **Why:** §5.1 says `k8s.alert_rules` is `cannot_answer` when the CRDs are absent, and the reliability
  review counts on that coverage; recording the absence as a refusal made that branch unreachable, and a
  cluster with PrometheusRule but no ServiceMonitor CRD refused the capability outright. Found by the
  review of milestone 4.
- **Status:** designer's choice; awaiting owner review.

## D-023: The capability tests leave room for thinking  (2026-11-01)

- **Choice:** the capability tests that read the reply (model, tool calling, streaming, structured) ask
  for at most 1024 tokens (`models.ANSWER_BUDGET`); connectivity and authentication keep 5 tokens (they
  only need an answer), limits keeps 16 (it checks the cap).
- **Why:** reasoning models spend tokens thinking before they write content; with 8 to 64 tokens qwen3:8b
  on Ollama returned empty content and failed `model`, `tool_calling` and `structured`, though it does all
  three. The budget is a ceiling: a model that answers at once still uses a few tokens.
- **Status:** designer's choice from the hosted test; awaiting owner review.
