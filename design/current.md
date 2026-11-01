# K8sHolmes: the design (0.1)

This is the design as it stands, written from the owner's brief of 2026-11-01
([`history/0001-owner-brief.md`](history/0001-owner-brief.md)) and accepted with it
([`changes/0001-initial-design.md`](changes/0001-initial-design.md)). Its §18 lists the guarantees the
tests check; §19 says what is built and what is still planned. The smaller choices are in
[`decisions.md`](decisions.md). Section numbers are stable: code comments and the skills refer to them
("design §6").

## 1. What it does

K8sHolmes is a self-hosted platform for **investigating** Kubernetes environments: what exists and how it
relates, why something broke, what is unused, unguarded or unmonitored, what changed before a problem,
what is exposed, and whether a change fixed anything. A user asks a question in plain words; an
**investigation** frames it, discovers what information and permissions exist, chooses **capabilities**
from a catalog, gathers **evidence** by calling them, tests explanations, and produces **findings** with
their evidence, their limits and next steps. The team's **knowledge** and **accepted exceptions** are
stored and respected. **Reports** are built from recorded data. A **model** the user brings (any
OpenAI-compatible endpoint) does the reasoning; code does the deciding: what may run, what counts as
evidence, what a number is.

### Goals

- One general investigation capability, not a hardcoded path per question (brief §2).
- Tool execution as the core: a catalog with purpose, validated inputs, permissions, predictable outputs
  and understood failures; permissions enforced by code, never by the model (brief §3).
- Evidence with provenance and **coverage**: "observed absent", "no data" and "cannot answer" are three
  different statements (brief §4).
- Findings that separate observation, hypothesis and reviewed conclusion, cite evidence for and against,
  state uncertainty, and carry no invented confidence numbers (brief §5).
- Exceptions and knowledge that are precise in scope, inspectable, and visibly influence conclusions
  (brief §6).
- Collaboration: challenge, add evidence, assign, continue; proposals with verification (brief §7).
- Saved and recurring investigations that report change, not repetition (brief §8).
- Bring-your-own-model with capability tests and visible usage (brief §9).
- A calm, precise interface (brief §10, §11); plain Python with clear boundaries (brief §12, §13).

### Non-goals (0.1)

Running inside the cluster or as an operator; alert routing; a tracing or metrics backend of its own;
multi-cluster fleets as one object (each cluster is an environment); applying infrastructure changes
(only the authorization path and the verification exist; see §11); security vulnerability scanning;
cost; a hosted service; telemetry.

### Responsibilities

K8sHolmes owns: the investigation engine, the capability model and its built-in Kubernetes
capabilities, evidence and findings, knowledge and exceptions, reports, the model client, persistence,
authorization and the web interface. It depends on the user's `kubectl` for the real environments and on
the user's model endpoint for reasoning. It imports nothing but the standard library and pydantic.

## 2. Concepts and words

The same words are used in code, the API, the interface and the docs.

| Word | Meaning |
|---|---|
| **Environment** | one Kubernetes cluster reached through a kubeconfig context, with its own permissions, inventory, knowledge, exceptions and capability settings |
| **Investigation** | one question in one environment with a scope and a time period, run as **steps**, ending in findings, limitations and next steps; it has a **status** (§6.5) and a **thread** of user messages and model turns |
| **Capability** | something the investigation can do: an id, a purpose, a **class** (`inspect`, `probe` or `change`), the systems it reaches, a validated input schema, the permissions it needs, an output with coverage; built in or added by an extension (§5) |
| **Call** | one execution of a capability: parameters, result, duration, error, the step and user it ran for; every call produces one piece of **evidence** |
| **Evidence** | something recorded from a source: what was asked (the call's parameters), what came back (the data, truncated if large), when, from which environment, with a hash and a retention class; it has a **coverage** statement (§7) |
| **Coverage** | what the evidence can say: `observed` (data shows it), `observed_absent` (data of this kind was available for this scope and period and shows none), `no_data` (the source has no data for this scope or period), `cannot_answer` (no connected source can answer this) |
| **Relationship** | one resource depends on or points at another, with a **basis**: `defined` (in configuration: a selector, a reference), `observed` (in operational data: endpoints, events), `inferred` (a name in an argument or an environment variable, incomplete evidence) |
| **Finding** | one claim about the environment (§8): a title and explanation, its **kind** (`observation`, `hypothesis`, `conclusion`), a **condition** code, affected resources, scope and period, evidence for and against, uncertainty and missing information, next steps, owner, **state**, and who authored it |
| **Exception** | a user's decision that a condition on an exact scope is intentional, with the explanation, approver, dates, reconsideration conditions and history (§9.1) |
| **Knowledge** | a scoped note the team provides (traffic expectations, criticality, ownership, maintenance windows, constraints, dependencies, lessons), kept apart from observed facts (§9.2) |
| **Report** | a structured document built from recorded data (§10): a live summary, a final report, an incident review, a reliability and monitoring review, a comparison |
| **Proposal** | a concrete improvement backed by findings, with its intended effect and verification criteria; a **verification** reruns the relevant capabilities and compares before and after (§11) |
| **Saved investigation** | a question with its scope and settings, rerun by hand or on a schedule; a **run** is one investigation made from it (§12) |
| **Model endpoint** | an OpenAI-compatible endpoint with its base URL, key, model name, limits and tested capabilities (§13) |

Times are ISO-8601 UTC. Durations are integer milliseconds. Ids are short, URL-safe and stable
(`inv_7f3a9c`, `ev_…`, `fnd_…`, `exc_…`), made with `secrets.token_hex`, never from `hash()`.

## 3. Architecture and modules

One Python package, one process, one SQLite file. The boundaries the brief asks for are modules, not
services:

```text
src/k8sholmes/
  records.py            every record of §2 as a pydantic model (extra="forbid"); the JSON of the API and the store
  store.py              persistence (§16): SQLite, one connection per thread, WAL, migrations by version
  auth.py               users, roles, sessions, environment grants, the audit log (§14)
  environments.py       environments, connection checks, the permission matrix, the inventory snapshot (§4)
  inventory.py          what each kind keeps in the inventory (§4.2)
  relationships.py      relationships with their basis: defined, observed, inferred (§4.3)
  cluster.py            the Cluster port, the kind table and the kubectl adapter (read-only verbs; §5.3)
  capabilities/         the capability model, the registry and the gate (§5)
    __init__.py         Capability, Context, Inputs, Result handling, the registry, validation, the gate
    kubernetes.py       the inspect capabilities that read one thing (inventory, get, list, events, logs, health, changes …)
    reviews.py          the review capabilities (network exposure, service backends, safeguards, alert rules)
    knowledge.py        knowledge and exceptions in scope, evidence of earlier investigations (§5.3)
    probes.py           the one probe (http.probe_url), off by default
    extensions.py       entry points and command capabilities from TOML (§5.5)
    builtin.py          the registry with every built-in capability and the loaded extensions
  engine.py             the investigation engine (§6): framing, discovery, gathering, concluding, follow-up
  thread.py             what the model sees: the system prompt, the schemas, the context, the thread (§6.2)
  prompts/              the system prompt and the output schemas the engine sends (plain text files)
  evidence.py           evidence records, coverage, redaction, size caps (§7)
  findings.py           validating a conclusion, findings, conditions, states, revisions (§6.3, §8)
  knowledge.py          knowledge notes and exceptions: matching, expiry, revocation, revisions (§9)
  reports.py            report kinds built from the store; Markdown and JSON export (§10)
  proposals.py          proposals and verifications (§11)
  saved.py  scheduler.py   saved investigations, schedules, run comparison, notifications (§12); the server's scheduler thread (§12, §16.3)
  models.py             the Model port, the OpenAI-compatible client, capability tests, usage, the secrets file (§13)
  app.py                the composition root: the home folder, the store, auth, the registry, the engine, the demo
  server.py             the web server (standard library), the static page and the event stream (§15)
  api/                  the JSON API, one module per resource; every route checks session, role and environment (§14.1)
  runner.py             investigations in background threads, their events for the stream, idempotency keys
  page/                 the interface: static HTML, CSS tokens, JavaScript modules, no build step (§15)
  cli.py                the command line (§15.5)
  testing/              FakeCluster, RecordedCluster, ScriptedModel, DemoModel, the demo cluster, contract checkers
```

Dependency direction: `page/` and `api.py` call the domain modules; the domain modules call `store.py`,
`cluster.py` and `models.py` through small interfaces; nothing imports `server.py`. The engine knows
capabilities only through the registry and the model only through the `Model` port.

## 4. Environments, inventory and relationships

### 4.1 Connecting

An environment is created with a name and a kubeconfig context (the kubeconfig path defaults to
`$KUBECONFIG` or `~/.kube/config`; only the context *name* is stored). Connecting runs `kubectl version`
and the permission matrix: `auth can-i` for every verb and kind the built-in capabilities use, per
namespace scope. The matrix is evidence (coverage `observed`) and decides which capabilities are
**available** in that environment; a capability whose permissions are missing is shown as unavailable
with the missing verbs, never silently skipped.

### 4.2 Inventory

The inventory is a snapshot of the scope (a namespace list or all namespaces the matrix allows):
Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Pods, Services, EndpointSlices, Ingresses,
NetworkPolicies, PersistentVolumeClaims, HorizontalPodAutoscalers, PodDisruptionBudgets, ServiceAccounts,
and the names (never the bodies) of ConfigMaps and Secrets. Each item keeps its kind, namespace, name,
labels, owner, creation time, and the fields the checks read. The snapshot records its time, the kinds it
could read, the kinds it could not (and why), and whether a list was truncated. Its **freshness** is the
snapshot time; **historical coverage** is the list of snapshots kept for the environment.

### 4.3 Relationships

Built by code from the inventory, each with its basis:

| Basis | Examples |
|---|---|
| `defined` | a Service's selector matching a workload's pod labels; a Deployment owning its ReplicaSets and Pods; an Ingress backend naming a Service; a PVC named in a pod spec; a ConfigMap or Secret referenced by name; a NetworkPolicy's pod selector; an HPA's or PDB's target |
| `observed` | a Service's EndpointSlices listing a pod's address; an event involving two resources |
| `inferred` | a Service name or URL in a container argument or environment variable value (names only; values that look like secrets are redacted first) |

Reports and findings show the basis beside every relationship; an impact question ("what is affected if
this fails") answers with `defined` and `observed` dependents first and lists `inferred` ones apart.

### 4.4 Connected sources

0.1 has one source kind, Kubernetes through `kubectl`. The capability model (§5) carries `systems` so
later sources (metrics, logs, alert managers) join as capabilities with their own coverage; until then,
questions about traffic, alerts fired or request paths answer with `cannot_answer` where only such a
source could know (§7.2).

## 5. The capability catalog

### 5.1 The model of a capability

```text
Capability
  id            "k8s.events"                      stable; area.name; never reused
  title         "Events in a scope"
  purpose       one paragraph: what it answers, what it does not
  class         inspect | probe | change
  systems       ["kubernetes"]                    what it reaches
  inputs        a pydantic model (extra="forbid"): every field typed and described; this is the JSON schema the model sees
  permissions   [("list", "events")]              verbs and kinds; checked against the environment's matrix
  run(inputs, context) -> Result                  context: environment, Cluster, store, user, time window
Result
  summary       one or two lines for the model and the activity view
  data          JSON, size-capped (§7.3); the evidence body
  coverage      observed | observed_absent | no_data | cannot_answer, with a one-line reason
  truncated     bool
  error         None, or a typed, explained failure
```

Rules: inputs are validated before anything runs, and a validation error is returned to the model as a
tool error with the field named; a capability never prompts, never reads credentials, never shells out
except through the `Cluster` port; `run` is deterministic for the same cluster state; `data` is JSON that
reads on its own (no references to in-memory objects).

### 5.2 Classes and the gate

| Class | Meaning | Who may run it |
|---|---|---|
| `inspect` | reads; changes nothing anywhere | a user with the `investigator` role in the environment |
| `probe` | an active test that sends traffic (an HTTP request to a URL); changes nothing in the cluster | `operator`; the environment must enable probes |
| `change` | changes infrastructure | `operator`; the environment must have an **authorization path** configured (§11.3); 0.1 ships no change capability |

The gate is code in the registry: the engine asks `registry.permitted(capability, user, environment)`
before every call, whatever the model asked. A refusal is a recorded call with the reason, is shown in the
activity, and makes the investigation `partial` if the question needed it. The model's instructions, and
text inside tool results, cannot widen the gate (§14.2).

### 5.3 Built-in capabilities (0.1)

All `inspect`, all over the `Cluster` port with the read-only verbs `get`, `list`, `logs`, `top`,
`auth can-i`, `version`, `api-resources`; the adapter refuses any other verb and any Secret body.

| Id | Answers | Coverage it can state |
|---|---|---|
| `k8s.inventory` | what exists in the scope and how it relates (§4.2, §4.3) | observed; per kind `no_data` when unreadable |
| `k8s.get` | one resource as JSON (Secrets: metadata only) | observed / observed_absent |
| `k8s.list` | resources of a kind in a scope, with a label or field selector, capped | observed / observed_absent |
| `k8s.events` | events in a scope or for one resource, in a period | observed / observed_absent / no_data (events expired) |
| `k8s.logs` | a container's log tail, current or previous, in a period | observed / no_data |
| `k8s.workload_health` | a workload's pods, their conditions, restarts, last terminations, probes, resources, the owner chain | observed |
| `k8s.changes` | what changed in a scope in a period: ReplicaSet revisions with their change cause and time, events of kind Normal with reasons like ScalingReplicaSet, resource `creationTimestamp`s, `generation` bumps | observed / no_data (events expired) |
| `k8s.network_exposure` | Services of type LoadBalancer or NodePort, Ingresses and their hosts, NetworkPolicies and which pods they select, pods selected by none | observed; always `cannot_answer` for anything outside the cluster (cloud firewalls, a mesh) |
| `k8s.service_backends` | a Service's selector, the pods it matches, their readiness, the endpoints; port alignment | observed / observed_absent |
| `k8s.safeguards` | per workload: probes, resource requests and limits, replica count, PDB, HPA, anti-affinity, image tag `latest`, ServiceAccount automount | observed |
| `k8s.alert_rules` | `PrometheusRule` and `ServiceMonitor` objects in the scope when the CRDs exist, and which workloads they reference by label | observed; `cannot_answer` when the CRDs are absent |
| `k8s.permissions` | the permission matrix of the environment | observed |
| `k8s.top` | CPU and memory of pods or nodes when a metrics server exists | observed / cannot_answer |
| `knowledge.search` | knowledge notes and exceptions whose scope touches the given resources | observed (of the store) |
| `evidence.recall` | evidence and findings of earlier investigations in the same environment and scope | observed (of the store) |
| `http.probe_url` | one HTTP request from the K8sHolmes host to a URL: status, latency, TLS expiry (`probe`, off by default) | observed |

### 5.4 Settings per environment

For every capability: enabled or disabled, a namespace allow-list, and caps (list size, log bytes, time
budget). **Test** runs the capability with sample inputs and shows its result and coverage before enabling
it. **Execution history** lists every call with its parameters, result size, duration, error, the
investigation and the user.

### 5.5 Extensions

Two documented ways, both loaded at start and shown in the catalog with their origin:

- a Python entry point in the group `k8sholmes.capabilities` returning `Capability` objects;
- a **command capability** declared in `$K8SHOLMES_HOME/capabilities/*.toml`: an executable from an
  admin's allow-list, an argument template whose placeholders are the validated inputs, a timeout, the
  class and the systems; the output is captured as text (or JSON when it parses). Templates never pass
  through a shell.

An extension that fails to load is listed as broken with the error; nothing else is affected.

## 6. The investigation engine

### 6.1 Phases

The brief's seven steps map to five phases, each persisted as it completes:

| Phase | Who | What |
|---|---|---|
| **framing** | model, structured | from the question, the environment and the user's scope: the question restated, the scope (namespaces, resources), the time period, the initial hypotheses, the capabilities it intends to use |
| **discovery** | code | the permission matrix, the available capabilities with their coverage promises, the knowledge and exceptions whose scope touches the framing, the latest inventory snapshot (made if stale) |
| **gathering** | model and code | the tool-calling loop (§6.2) |
| **concluding** | model, structured, then code | findings, limitations, next steps, the knowledge used; then validation (§6.3), exception matching (§9.1) and de-duplication (§8.3) |
| **follow-up** | user, then model | a message, more context or a requested check starts another gathering and concluding round; the earlier findings are revised, never silently replaced (§8.4) |

### 6.2 The gathering loop

Each turn the model sees: the system prompt (the role, the rules of evidence and coverage, the rules of
§14.2), the framing, the discovery, the enabled capabilities as tools (their input schemas), the knowledge
and exceptions in scope marked as such, and the thread so far. It calls tools; the engine validates the
inputs, asks the gate, runs the capability, records the call and its evidence, and returns the result's
summary and data (capped) as the tool message. Budgets are settings per investigation: steps (default
24), calls per step (8), tokens, wall time. When a budget ends, the loop stops and the investigation
concludes with what it has, marked `partial` with the reason. Contradictions are the model's job to
handle; the engine makes them visible by keeping every result, including the ones that contradict the
framing's hypotheses.

### 6.3 Validation of the conclusion

The concluding reply is structured (a JSON schema). Code checks it:

- every evidence id a finding cites exists in this investigation (or is recalled evidence from the
  store); a finding with no supporting evidence is refused;
- a finding's `kind` is `observation` or `hypothesis`; only a user marks a `conclusion` (§8.1);
- a finding whose coverage statements are `no_data` or `cannot_answer` on the point it claims is turned
  into a limitation, not a finding ("unused" without traffic data; "isolated" from cluster checks alone;
  a reconstructed path presented as traced);
- no confidence percentages or invented numbers: a number in a finding must appear in cited evidence;
- knowledge the reply says it used must have been in the context.

Problems go back to the model once as a repair request; a second failure keeps the model's text as a
limitation and the investigation completes as `partial`.

### 6.4 Control

- **Cancel** stops at the next step boundary; completed steps, calls and evidence stay; status
  `cancelled`; a cancelled investigation can be **resumed**.
- **Redirect**: a user message during gathering is appended to the thread and the loop continues with it
  on its next turn; the user may also change the scope or period, recorded as a revision of the framing.
- **Resume** after a cancel, an interruption (the server stopped) or a `partial` end: the loop continues
  from the last completed step with the same budget rules; a step that was running when the process died
  is marked `interrupted` and its call, if it ran, is kept.
- **Revisit**: every investigation stays readable with its thread, steps, calls, evidence, findings and
  reports; another user with access can continue it.

### 6.5 Status

`queued` → `running` → `completed` | `partial` | `cancelled` | `failed`; `interrupted` when the server
stopped mid-step; `resumed` runs appear as `running` again. `completed` means the model concluded and
validation passed with every needed capability available; `partial` means it concluded with a stated
limitation (a budget, a refusal, an unavailable source, a failed validation); `failed` means the model or
the store failed and nothing could be concluded. **Completion is not recovery**: a finding's state (§8.2)
and a verification (§11) say whether anything was fixed.

## 7. Evidence and coverage

### 7.1 The record

```text
Evidence
  id, investigation_id, environment_id, step, call_id
  source        the capability id and the systems it reached
  query         the validated inputs, as sent
  collected_at  when the result came back
  data          the result, redacted (§14.3), capped (§7.3); a sha256 of the full data before capping
  coverage      the statement and its reason
  retention     "investigation" (deleted with the investigation) | "pinned" (kept while cited by a reviewed finding or an exception) | "short" (purged by the retention policy, §16.3)
```

A failed call is evidence too (`error` set, coverage `no_data` or `cannot_answer` with the reason), so a
report can say what could not be read. Evidence is never edited; a correction is new evidence.

### 7.2 Coverage rules the engine enforces

| Question shape | Allowed statement | Not allowed |
|---|---|---|
| "unused" | `observed_absent` only with a traffic or request source over a stated period; with Kubernetes alone: "no endpoints", "no dependents defined", `cannot_answer` for traffic | calling a service unused because no traffic data exists |
| "isolated" | what the cluster shows: exposure, policies, pods selected by none; `cannot_answer` for everything beyond the cluster | "isolated from external networks" |
| "the path of this failure" | a chain of `defined` and `observed` relationships with the events and logs that support each hop, labelled as reconstructed | "the trace shows" |
| "what changed" | `observed` changes in the period, and `no_data` when events have expired for part of it | silence about the gap |
| "without an alert" | only with an alert source; otherwise the problems found, and `cannot_answer` for whether alerts fired | "no alert fired" |

### 7.3 Size and redaction

`data` is capped (default 256 KiB per evidence, 2000 lines of logs); the cap is recorded as `truncated`
with the full hash. The redactor of §14.3 runs before storage and before any model message.

## 8. Findings

### 8.1 The record

Title, explanation, `kind` (`observation`: what the evidence shows; `hypothesis`: an explanation
consistent with the evidence; `conclusion`: a hypothesis a user has reviewed and marked), `condition` (a
stable code such as `replicas.single`, `probe.missing`, `service.no-backends`, `limits.missing`,
`exposure.loadbalancer`, `policy.unselected-pods`, `alert.missing`, `change.before-incident`, or
`custom.<slug>` for what the model names itself), `resources` (kind, namespace, name), `environment_id`,
`period`, `evidence_for`, `evidence_against`, `uncertainty`, `missing_information`, `next_steps`, `owner`,
`state`, `author` (`model` with the model name, or a user), `knowledge_used`, `exception_id` when one
applied, `created_at`, `revisions`. Confidence is words, never a number.

### 8.2 States

`open` → `acknowledged` → `fixed` → `verified`; `open` → `accepted_risk` (through an exception, §9.1).
Transitions are user actions recorded with who and when; `verified` is set only by a verification (§11)
or by a user who cites the evidence; `fixed` is a claim, `verified` is evidence.

### 8.3 De-duplication

A finding's key is (`environment`, `condition`, sorted `resources`). Within one investigation the same key
is one finding with merged evidence. Across runs of a saved investigation (§12) the key identifies an
unchanged, new or resolved finding. The key never groups different conditions or different resources.

### 8.4 Revisions and authorship

Every edit to a finding (a user's correction, a follow-up's revision by the model, a state change) is a
revision with the author and time; the interface shows model text and human text apart (a byline on each
paragraph) and the current version with its history behind an "earlier versions" control.

## 9. Knowledge and exceptions

### 9.1 Exceptions

An exception is created from a finding ("accept as intentional") or on its own:

```text
Exception
  id, environment_id
  condition        the condition code accepted (one)
  scope            kind, namespace, name: exact; or namespace-wide for one kind when `applies_to = "namespace"` is chosen explicitly
  explanation      the user's words
  approved_by, approved_at
  review_at        optional: the interface asks for a review from this date
  expires_at       optional: not applied after this date
  reconsider_when  text, and optional structured triggers: `replicas_change`, `owner_change`, `image_change`, `resource_deleted`
  revisions        every edit, with author and time
  revoked_at, revoked_by, revocation_reason
```

Matching, in code, when an investigation concludes: a finding is covered when the environment, the
condition and every resource match an exception that is not expired and not revoked; a covered finding is
kept with state `accepted_risk`, `exception_id` set, and is shown under "accepted" rather than as a new
problem. An expired or revoked exception that would have matched is shown as such ("an exception expired
on …"). A trigger that fired (the replicas changed since approval) reopens the finding with the exception
named. Exceptions never match a different condition, a different resource, or a similar name.

The Knowledge page lists exceptions by environment with filters (active, due for review, expired,
revoked), and edits, extends, expires or revokes them; every change is a revision.

### 9.2 Knowledge notes

```text
Knowledge
  id, environment_id
  kind       traffic | criticality | ownership | maintenance | constraint | dependency | lesson
  scope      kind, namespace, name (any may be empty: the note applies to the whole environment or namespace)
  text       the note
  author, created_at, updated_at, revisions
  source     "user" | "investigation <id>" (a reviewed lesson promoted from a finding)
```

Notes in scope are given to the model in a section titled as user-provided knowledge, apart from evidence.
The conclusion must list the notes it used; findings and reports show "influenced by" with the note and
its author. A note is never evidence: a finding may cite it under `knowledge_used`, not under
`evidence_for`.

## 10. Reports

All reports are built by code from the store; a model writes prose only inside findings. Every number
(counts, durations, percentages of workloads with a safeguard) is computed from records, and the report
names the records it counted.

| Kind | Content |
|---|---|
| **live summary** | while an investigation runs: the question, status, steps so far, calls made, the most useful finding so far, what is happening now |
| **investigation report** | the question, scope and period; the answer (the top finding or the stated limitation); findings by kind and state with their evidence; limitations and coverage gaps; next steps; the model and its usage; the knowledge used; the thread; appendix: every call |
| **incident review** | for an investigation marked as an incident: the timeline built from evidence times (events, revisions, log lines), the reconstructed path with its bases, what was found, what was not known, proposals |
| **reliability and monitoring review** | for a scope: the inventory's workloads against the safeguards of `k8s.safeguards` and the alert rules of `k8s.alert_rules`, as a table with counts computed by code; exceptions applied; coverage (`cannot_answer` when no alert source exists) |
| **comparison** | two runs of one saved investigation: new, resolved and unchanged findings by key (§8.3), evidence that changed, the models used |

Reports have revisions: regenerating one makes a new revision; a user's edits (an added paragraph, a
corrected title) are stored as edits on the revision with the author, shown apart from generated text.
Reports are readable after the workload is gone, because they embed the evidence they cite.
**Shareable views**: a share link carries a token, an expiry and a permission (view only), is listed and
revocable in the report's settings, and is audited; downloads are Markdown and JSON made by code.

## 11. Proposals and verification

### 11.1 Proposals

Made from findings when the evidence supports one: `kind` (`config_change` with the manifest change as a
diff or a `kubectl` command, `monitoring` with the rule or probe to add, `task` for people), `intended
effect`, `verification` (the capabilities to rerun, the condition that must then be `observed_absent`, the
period to wait), `state` (`proposed`, `approved`, `applied`, `verified`, `rejected`), the findings it
addresses, author and revisions. K8sHolmes 0.1 never applies a proposal; the user does, and records it as
`applied` (with what they did).

### 11.2 Verification

Reruns the proposal's capabilities in the same scope, compares the new evidence with the evidence the
findings cited (before and after, side by side, with the times), and records a verification: `verified`
when the condition is `observed_absent` after the change, `not_verified` with what still shows otherwise.
A verification sets the findings to `verified` and nothing else; it never closes an investigation and
never claims an alert cleared.

### 11.3 The authorization path for changes

A `change` capability runs only when the environment has `changes.authorized_by` set to one of
`"operator_role"` (an `operator` must start it and confirm it in the interface) or `"approval"` (an
`operator` requests, an `admin` approves, the call runs after approval), and the capability is enabled.
Without it the gate refuses with the message that says how to configure it. 0.1 ships the gate and no
change capability.

## 12. Saved and recurring investigations

A saved investigation keeps the question, the environment, the scope, the period rule ("the last 24
hours"), the model and the budgets. **Run now** makes an investigation from it; a **schedule** (an
interval in hours, or a daily time) runs it from the server's scheduler thread; each run is a normal
investigation linked to the saved one. After a run, the comparison with the previous run (§10) is made by
code, and the run's summary says what changed: new findings, resolved findings, findings whose evidence
changed; unchanged findings are counted, not repeated; accepted exceptions apply (§9.1); duplicates group
by key (§8.3). A run with no meaningful change produces a one-line result. **Notifications**: a webhook
URL per saved investigation, with a frequency (`every_run`, `changes_only`, `daily_digest`); the payload
is the summary and a link, never evidence bodies.

## 13. Models

### 13.1 Endpoints

```text
ModelEndpoint
  id, name, base_url, model, api_key (in the secrets file, §14.3), headers (optional)
  limits        max_tokens per reply, timeout_ms, tokens per investigation, tokens per day
  price         optional input and output price per million tokens, for usage display
  capabilities  the results of the last capability test (§13.2)
  default       one endpoint is the default; an environment may name its own
```

The `Model` port: `complete(messages, tools, schema, stream) -> Reply`, with usage (tokens, duration);
the adapter is the OpenAI-compatible chat completions API over `urllib`, with tool calls, streaming
(server-sent events) and structured replies (`response_format` with a JSON schema, falling back to a JSON
instruction when unsupported, recorded as such).

### 13.2 Capability tests

Run from Settings and shown with their time: connectivity (the models list or a one-token completion),
authentication, model selection (the named model answers), **tool calling** (the model is given one tool
and a request that needs it, and must call it with the right arguments), **streaming** (chunks arrive),
**structured replies** (a schema is honoured), limits (a reply within `max_tokens`). Each result is
`passed`, `failed` (with the response) or `unsupported`. The page says plainly that passing tests does not
prove the model investigates well, and links the comparison (§13.4). An endpoint whose tool-calling test
failed cannot be chosen for investigations.

### 13.3 Visibility and no silent switching

Every investigation shows its endpoint and model, and its usage (calls, tokens, duration, price when
known). Every model call is logged with the endpoint id. If an endpoint fails, the investigation fails or
pauses with the error; it never retries on another endpoint. Changing an investigation's model is a user
action recorded in the thread.

### 13.4 Comparing models

A **case** is a preserved, reviewed investigation (its evidence and the conclusion a user reviewed).
A **fixed-evidence comparison** runs only the concluding phase of each chosen endpoint on the case's
evidence and shows the findings side by side with the reviewed ones, with the validation results (§6.3)
and usage. A **free comparison** runs the whole investigation again per endpoint in the same environment
and is labelled as such, since the models chose their own calls and the environment may have changed.

## 14. Access control, privacy and audit

### 14.1 Users and roles

Local accounts (a username, a password hashed with `hashlib.scrypt`), sessions as HTTP-only cookies.
Roles per environment: `viewer` (reads investigations, reports, knowledge), `investigator` (runs
investigations with `inspect` capabilities, edits findings, writes knowledge, accepts exceptions),
`operator` (also probes, proposals, verifications, change requests), `admin` (also environments, models,
capabilities, users, retention, shares). The first start prints a one-time setup link that creates the
first admin. Every API call checks the session, the role and the environment; evidence, knowledge,
reports and exports are read through the same check; caches and stored results are keyed by environment.

### 14.2 Data, not instructions

Tool results, logs, documents and knowledge are placed in tool and context messages, never in the system
prompt, and the system prompt says they are data. More importantly, nothing the model says can run
anything the gate refuses (§5.2); an instruction inside a log line that asks for a change capability
produces a refused call, recorded and shown.

### 14.3 Secrets

Model keys live in `$K8SHOLMES_HOME/secrets.json` (mode 0600) or in an environment variable named in the
endpoint, never in the database, never in a model message, a report, an export or a log. The kubeconfig
is read by `kubectl`, never copied. The redactor runs on every result before storage and before any model
message: environment variable values whose names contain `key`, `token`, `password`, `secret`,
`credential`; values shaped like JWTs, cloud keys, private keys or basic-auth URLs; `Authorization`
headers in logs. Redactions are counted on the evidence. Secret bodies are never fetched.

### 14.4 Audit and retention

The audit log records: sign-ins, environment and model changes, capability enabling and tests, every
refused call, exception creation, edit and revocation, finding state changes, proposal and verification
actions, shares created and revoked, exports, retention purges. Retention settings per environment: days
to keep `short` evidence, days to keep investigations that no reviewed finding, exception, report or saved
run references; a nightly purge that writes what it removed to the audit log. Pinned evidence (§7.1) is
never purged while referenced.

## 15. The interface and the command line

### 15.1 Navigation

Five destinations, always visible: **Investigate** (home), **Investigations**, **Capabilities**,
**Reports**, **Knowledge**; **Settings** behind the account menu (environments, models, users, retention,
audit). The home page asks "What would you like to investigate?", offers a handful of starting questions
(the brief's examples) that fill the field without limiting it, and shows the environment, the scope and
the model that will be used.

### 15.2 An investigation

The first screen shows four things: the question and scope; the status with the current step in one
line; the most useful finding or answer; the next meaningful action (resume, accept, propose, follow up).
Tabs below: **Findings** (by kind and state, accepted ones folded), **Activity** (steps and calls, each
opening to parameters, result, duration, error), **Evidence** (every record with its coverage and
redactions), **Report**, **History** (revisions, the thread). An inspector on the right opens a selected
finding, call or evidence without leaving the page. Live updates arrive as server-sent events.

### 15.3 States and accessibility

Every list and page has designed loading, empty, interrupted, partial, permission-denied and error
states; a failed action keeps the user's text and the page's context. Everything is reachable by
keyboard with visible focus; text contrast meets WCAG AA on every surface; the layout works from a phone
width to a wide desk. No external fonts or scripts are loaded.

### 15.4 Brand and tokens

The name is K8sHolmes everywhere. The mark is a geometric H made of two bracket-like forms facing each
other with a small diamond at the centre, as inline SVG. Tokens in `page/tokens.css`:

| Token group | Values |
|---|---|
| surfaces | graphite `--bg-0 #16181b`, `--bg-1 #1c1f23`, `--bg-2 #23272c`, `--bg-3 #2b3036`; borders `--line #353b42` |
| text | ivory `--text-1 #f1ece2`, neutral `--text-2 #a8a9a3`, muted `--text-3 #777a76` |
| accent | copper `--accent #c47a4a`, hover `#d48b5b`, on-accent `#16181b` |
| states | sage `--ok #7fa784`, amber `--review #c9a24d`, red `--severe #c25a4e` |
| type | a system sans stack at 14px base, line height 1.5; monospace for identifiers, parameters and output; four sizes (12, 14, 16, 20) |
| space, radius, border | 4px steps; radii 4 and 6; one-pixel separators |
| interaction | focus ring 2px copper offset 2px; selection background `--bg-3`; disabled at 50% |

Prefer alignment and whitespace to cards; no gradients, glows, oversized headings or decorative charts; a
chart appears only where the brief's reports need a count over time.

### 15.5 The command line

```text
k8sholmes [--home DIR] <command>                      # the home: $K8SHOLMES_HOME or ~/.k8sholmes
k8sholmes serve [--host 127.0.0.1] [--port 8800]       # the web interface; another host needs --allow-remote and TLS in front
k8sholmes setup                                       # prints the one-time link to create the first admin
k8sholmes demo [--after-fix] [--question "…"]          # the demo environment and its offline investigator (§17.2)
k8sholmes investigate "QUESTION" --env NAME [--model NAME] [--namespace NS]… [--user NAME] [--json]
k8sholmes capabilities [--env NAME] [--json]          # the catalog, with availability in an environment
k8sholmes environments                                # the environments and when they were connected
k8sholmes export INVESTIGATION_ID --out DIR           # report.md, report.json, evidence/
```

Exit codes: 0 success, 1 failure with a message that says what to check, 2 usage. Never prompts for
secrets; never prints them.

## 16. Persistence and reliability

### 16.1 The store

One SQLite file, `$K8SHOLMES_HOME/k8sholmes.db` (default home `~/.k8sholmes`), WAL mode, foreign keys on,
one connection per thread, schema versioned (`schema_version` table; migrations are numbered SQL files
applied in order at start; a newer schema than the code knows is refused with a message). Records are
rows with typed columns for what is queried and a JSON column for the rest, validated through
`records.py` on the way in and out. Blobs (evidence data) are capped text in the same file.

### 16.2 Interruptions and retries

Every step, call and evidence row is committed when it completes; the investigation row's status and
`current_step` are updated in the same transaction. A server starting finds `running` investigations and
marks them `interrupted` with the step; the user resumes them (§6.4). A model call is retried only for
retryable failures (timeouts, HTTP 429 and 5xx, a connection reset), at most twice with backoff, on the
same endpoint; permanent failures (4xx other than 429, a malformed reply after the repair) end the step
with the error. A capability call is never retried by the engine; the model may ask again, and both calls
are recorded. Operations started by the interface carry an idempotency key, so a repeated click does not
start two investigations.

### 16.3 Retention

§14.4. The purge runs in the scheduler thread, in small transactions, and never touches rows referenced
by a reviewed finding, an exception, a report revision or a saved run.

## 17. Testing and demonstration scenarios

### 17.1 Suites

The default suite is offline, fast and deterministic: `FakeCluster` (resources as dicts),
`RecordedCluster` (replayed `kubectl` requests and replies), `ScriptedModel` (replies by rule or by
script, including tool calls), a temporary store. Contract tests check every `Cluster` and `Model`
adapter and fake. Acceptance tests (`tests/e2e/test_ac<N>_*`) use the public API and CLI. `tests/cluster/`
(marker `cluster`) runs against a throwaway local cluster with manifests that reproduce the demo
scenarios; `tests/hosted/` (marker `hosted`) runs one real model through the capability tests.

### 17.2 The demo

`k8sholmes demo` creates the environment **demo** from a recorded cluster (`k8sholmes/testing/demo/`):
a small shop (`web`, `api`, `worker`, `scheduler`, `legacy-export`, `postgres`) with planted conditions,
and a scripted model that investigates them. The scenarios, each with its expected findings and known
limitations, are in `docs/demo.md` and tested:

1. "Why is `api` restarting?" → `pod.crashloop.oom` on `api` (observed), the limit raised in a revision
   before the restarts (observed change), a proposal to raise the limit; verification after the recorded
   "after" state.
2. "Which services appear unused?" → `legacy-export` has no backends and no defined dependents
   (observed), and traffic is `cannot_answer`; the finding says so.
3. "Is this environment isolated?" → the LoadBalancer on `web`, pods selected by no policy, and
   `cannot_answer` beyond the cluster.
4. "Which important alerts are missing?" → the reliability review on the recorded `PrometheusRule`s:
   `api` and `worker` have none; `scheduler` runs one replica (then the user accepts that as intentional,
   and the next run shows it as accepted).
5. "What changed before the problem?" → the `k8s.changes` window.

## 18. Guarantees (acceptance cases)

Each is a test `tests/e2e/test_ac<N>_<slug>.py` through the public API or CLI that fails if the feature
is removed.

1. **The gate is code.** A model that asks for a disabled, unpermitted or `change` capability gets a refused
   call, recorded and shown; the investigation ends `partial` with the refusal as a limitation, never
   `completed` as if nothing was missing.
2. **Inputs are validated.** A tool call with a wrong or extra field is refused with the field named,
   before anything runs.
3. **Findings cite evidence.** A conclusion naming an evidence id that does not exist is sent back once for
   repair; a finding without supporting evidence is never stored.
4. **Coverage is honest.** With Kubernetes as the only source, "unused" yields `cannot_answer` for traffic
   and the finding says so; "isolated" yields `cannot_answer` beyond the cluster; a reconstructed path is
   labelled reconstructed.
5. **Steps persist.** A process killed during gathering leaves the completed steps, calls and evidence;
   the investigation is `interrupted` and resumes from the last completed step.
6. **Cancel keeps work.** Cancelling stops at the next step boundary; everything so far stays; resume
   continues.
7. **Follow-up continues.** A user message after completion starts another round that sees the earlier
   thread and evidence; earlier findings get revisions, not replacements.
8. **Exceptions apply exactly.** An accepted finding is `accepted_risk` in the next investigation of the
   same scope; a different resource, a different condition or a similar name is not covered; an expired
   or revoked exception is not applied and is named.
9. **Exceptions are inspectable.** Every edit and the revocation of an exception are revisions with author
   and time, listed on the Knowledge page.
10. **Knowledge influence is shown.** A note in scope that the conclusion used appears as "influenced by"
    on the finding and in the report; a note is never cited as evidence.
11. **Numbers come from records.** Every count in a report equals the number of records it names; a
    model's text cannot change a count.
12. **Edits are distinguishable.** A user's edit to a finding or report is a revision with a human byline;
    the model's text stays readable in history.
13. **Reruns report change.** The second run of a saved investigation lists new, resolved and unchanged
    findings by key; with no change, its summary is one line.
14. **Model tests tell the truth.** An endpoint whose model does not call tools fails the tool-calling test
    with the response shown and cannot be chosen; an endpoint failure never switches to another endpoint.
15. **Secrets never leave.** A planted token in an environment variable, a log line and an argument appears
    in no evidence, no model message, no report, no export and no log; the redaction count says how many.
16. **Roles hold.** A viewer cannot start an investigation; an investigator cannot read another
    environment's investigations, evidence, knowledge or reports; an admin can.
17. **Actions are audited.** Sign-in, a refused call, an exception's creation and revocation, a finding's
    state change, a share and an export each produce an audit row.
18. **Verification is evidence.** A proposal's verification reruns its capabilities, records before and
    after, and sets `verified` only when the condition is `observed_absent`; investigation completion and
    verification are separate states.
19. **Changes need a path.** A `change` capability is refused unless the environment's authorization path is
    configured and the user is an operator.
20. **Relationships carry their basis.** Every relationship in an inventory or report is `defined`,
    `observed` or `inferred`, and an impact answer lists inferred dependents apart.
21. **Loopback by default.** `serve` binds `127.0.0.1` unless `--allow-remote`; a share link is view-only,
    expires, and is refused after revocation.
22. **The demo runs offline.** `k8sholmes demo` and the five scenarios of §17.2 produce their expected
    findings and limitations with no cluster and no model endpoint.
23. **Data is not instructions.** An instruction planted in a recorded log line that asks for a change
    capability results in a refused call and no change.
24. **Retention is precise.** The purge removes only evidence and investigations outside the policy and
    never anything referenced by a reviewed finding, an exception, a report revision or a saved run; the
    audit log says what it removed.

## 19. Implemented and planned

Updated as milestones land.

**Implemented (milestone 1, 2026-11-01):** the records (§2) and the SQLite store with its versioned
schema (§16.1); users, roles, sessions and the audit log (§14.1, §14.4); the `Cluster` port with the
read-only `kubectl` adapter, recordings, `FakeCluster` and the contract checker (§5.3); environments with
the permission matrix, the inventory and relationships with their basis (§4); the capability model, the
registry, the code gate and execution into calls and evidence (§5.1, §5.2); the sixteen built-in
capabilities (§5.3); extensions through entry points and command capabilities (§5.5); evidence with
coverage, the redactor and size caps (§7, §14.3); knowledge notes and exceptions with exact matching,
expiry, revocation and revisions (§9); the engine's five phases, validation of the conclusion, budgets,
cancel, resume, follow-up and interruption marking (§6, §16.2); findings with kinds, states, keys and
revisions (§8); the `Model` port, the OpenAI-compatible client with tool calls, streaming, structured
replies, typed failures and the capability tests (§13.1, §13.2, §13.3); the demo environment and its
offline investigator (§17.2); the command line's `investigate`, `capabilities`, `environments`, `demo`
and `export` (§15.5, with a plain-text report until §10 lands). Guarantees 1 to 10, 15, 19, 20, 22 and 23
are tested.

**Implemented (milestone 2, 2026-11-01):** the web server on the loopback interface, the JSON API with the
session, role and environment check on every route, server-sent events for a running investigation,
investigations and follow-ups in background threads with idempotency keys (§15, §16.2); the interface:
Investigate, Investigations, the investigation page (answer first, findings, activity, evidence, history,
an inspector, accept as intentional, follow-up, cancel and resume), Capabilities (catalog, settings, test,
history), Knowledge (exceptions with their history, notes), Settings (environments, models with the
capability tests, users and grants, retention, audit), the tokens and the mark (§15.1 to §15.4); `serve`
and `setup` (§15.5). Guarantees 14, 16, 17 and 21 are tested.

**Implemented (milestone 3, 2026-11-01):** reports built from records with revisions, a person's edits
kept apart, Markdown and JSON downloads and view-only share links: the live summary, the investigation
report, the incident review, the reliability and monitoring review and the comparison (§10); proposals with
their states, applied by people and verified by rerunning their capabilities, `verified` only when the
condition is observed absent, the findings set to `verified` and nothing else (§11.1, §11.2); saved
investigations run by hand or from the server's scheduler thread, compared with the run before by finding
key, a one-line result when nothing changed, webhook notifications carrying the summary and a link (§12);
the fixed-evidence model comparison (§13.4; a free comparison is an investigation started with another
endpoint); the retention purge from the scheduler thread, never touching pinned or referenced rows (§16.3);
the Reports page, the Report and Proposals tabs, saved investigations under Investigations (§15).
Guarantees 11, 12, 13, 18 and 24 are tested.

**Implemented (milestone 4, 2026-11-01):** the cluster suite (`tests/cluster/`, marker `cluster`): the
demo shop as manifests applied to a fresh namespace of a throwaway kind or minikube cluster, the five
scenarios of §17.2 asserted on coverage, condition codes and citations, a planted Secret that never reaches
evidence, recording with `K8SHOLMES_RECORD_TO`; the hosted test (`tests/hosted/`, marker `hosted`): one
real OpenAI-compatible endpoint through the capability tests of §13.2; the user documentation (setup, the
command line, capabilities, extensions, records and exports, privacy, the demo) and four runnable examples,
all executed by the default suite. Guarantees 1 to 24 are tested on the recording; the cluster suite runs
them on a real cluster by hand.

**Planned:** nothing before 0.1.0 is released; what comes after is a new change record.

## 20. Known limits (0.1)

One source kind (Kubernetes through `kubectl`); traffic, fired alerts and request paths are
`cannot_answer` unless a later capability brings a source; no `exec` into pods; CRDs are opaque JSON
except `PrometheusRule` and `ServiceMonitor`; one process and one SQLite file (a few concurrent users, not
a fleet); local accounts only; schedules run only while the server runs.
