# k8sholmes: the design (proposed, 0.1)

This is the design as proposed in [`changes/0001-initial-design.md`](changes/0001-initial-design.md) on
2026-11-01. **It is not accepted**: nothing here is built, and the owner's answers to the open questions
in [`decisions.md`](decisions.md) may change any part of it. Once accepted, this page becomes the design as
it stands, and its §14 lists the guarantees the tests check. Section numbers are stable: code comments and
the skills refer to them ("design §4").

## 1. What it does

k8sholmes investigates a failure in a Kubernetes cluster the way an experienced engineer would, and writes
the investigation down. Given a **symptom** (a pod, a deployment, a service, a job, or a whole namespace)
it **gathers evidence** read-only (§4), runs a **catalogue of checks** for the known failure patterns
(§5), asks a model **once** to reason over the whole case when the checks do not explain the symptom
(§6), and writes a **case file** (§7): findings with their evidence and grade, a root cause, and
**remedies** as exact commands it never runs (§8). A command line (§9), a Python API (§10) and a local
dashboard (§11) share the same case files.

### Goals

- A first answer in seconds from the checks alone, offline from any model, deterministic.
- Every finding names its evidence; every piece of evidence is kept as returned, with its hash and time.
- One whole-case model call at most, structured, checked by code, recorded with what was sent.
- Provably read-only: one adapter, a short list of verbs, Secrets never read.
- A case file that opens without the cluster, later, elsewhere.

### Non-goals (0.1)

Changing the cluster; running in the cluster; alert integration; security scanning; cost; multi-cluster;
reading observability stacks; a hosted service; telemetry.

### Responsibilities

k8sholmes owns the ports it needs (`Cluster`, `Reasoner`), the check catalogue, the case file format and
the report. It depends on `kubectl` being installed for the real adapter, and on nothing else at import
time.

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Symptom** | what the user points at: `pod/<name>`, `deployment/<name>`, `service/<name>`, `job/<name>`, `node/<name>`, or a namespace; with the context and namespace it was read in |
| **Evidence** | one thing read from the cluster: a resource (JSON as returned), a list of events, a slice of logs, a `top` reading; with `id`, `kind`, `source` (the exact request), `read_at`, `sha256`, and `redactions` (what was removed, §12) |
| **Grade** | how far to trust a statement: `observed` (read from the cluster), `derived` (computed by a check from observed evidence), `reported` (said by a model), `inferred` (a suggestion no evidence confirms) |
| **Check** | a plain function with a stable id (`area.pattern[.cause]`), the evidence kinds it needs, and the findings it returns; §5 |
| **Finding** | one claim: `check`, `severity` (`blocking`, `degraded`, `warning`, `info`), `grade`, `subject` (the resource), `message` in plain words, `evidence` (ids it rests on), `remedies` |
| **Root cause** | the finding, or the model's narrative over several findings, that explains the symptom; always with the grade of what it rests on |
| **Remedy** | an exact command or manifest change, with `risk` (`safe`: reads or restarts nothing; `restarts`: pods restart; `changes`: the workload's definition changes; `destructive`: data or resources are removed), never run by k8sholmes |
| **Case** | one investigation of one symptom: the symptom, the evidence, the findings, the reasoning, the report; a folder (§7) |

Times are ISO-8601 UTC; durations are integer milliseconds; ids are `hashlib` digests, never `hash()`.

## 3. The `Cluster` port

A small `typing.Protocol` owned by k8sholmes. Everything the package knows about a cluster comes through
it, so the default suite runs on recordings and the real adapter is the only place `kubectl` is started.

```text
class Cluster(Protocol):
    context: str                       # the kubeconfig context name, for the case file
    def get(self, kind, name, *, namespace=None) -> dict          # one resource as JSON; raises NotFound
    def list(self, kind, *, namespace=None, selector=None, field_selector=None) -> list[dict]
    def events(self, *, namespace=None, involved=None) -> list[dict]   # kind/name, sorted by last time
    def logs(self, pod, *, namespace, container=None, previous=False, tail=200) -> str
    def top(self, kind, *, namespace=None) -> list[dict] | None  # pods or nodes; None when metrics are absent
    def can_i(self, verb, kind, *, namespace=None) -> bool
    def version(self) -> dict                                    # client and server versions
```

Adapters:

- **`Kubectl`** (core, no extra): runs `kubectl` as an argument list with `-o json`, a timeout per call
  (default 20 s), `--context` and `--namespace` passed explicitly, never through a shell. It knows the
  allowed verbs of §4 and nothing else. It is also the recorder: with `record=<folder>` every request and
  reply is written down for the tests.
- **`RecordedCluster`** (`k8sholmes.testing`): replays a recording; a request the recording lacks raises
  with the request shown, so a test never silently reads nothing.
- **`FakeCluster`** (`k8sholmes.testing`): resources given as Python dicts, for unit tests of checks.

A contract checker in `k8sholmes.testing` checks every adapter: `NotFound` for a missing resource, sorted
events, `None` from `top` without metrics, the Secret rule of §12.

## 4. Evidence gathering

### 4.1 Allowed verbs

The `Kubectl` adapter can run exactly these: `get`, `logs`, `events` (or `get events`), `top`, `auth
can-i`, `version`, `api-resources`. Nothing else exists in the package; a test greps the source for every
`kubectl` verb used. The adapter refuses `get secret`, `get secrets` and any request for a Secret's body
(§12).

### 4.2 The walk

Evidence is gathered from the symptom outward, by code, with a budget (default 60 requests, 30 s):

| Symptom | What is read |
|---|---|
| pod | the pod; its events; current and previous logs of every container (tail 200 lines, 64 KiB each); its owner chain (ReplicaSet, Deployment, StatefulSet, DaemonSet, Job, CronJob) and their events; the node (conditions, allocatable, taints); PVCs it mounts, their PVs and StorageClasses; the names of ConfigMaps and Secrets it references and whether each exists (names only); `top pod` when available |
| deployment / statefulset / daemonset | the resource and its events; its ReplicaSets or revision history; the pods it selects (and the pod walk for the unhealthy ones, at most 3) |
| service | the Service; its EndpointSlices; the pods its selector matches (and the ports they expose); Ingresses that point at it; NetworkPolicies in the namespace |
| job / cronjob | the resource, its pods, their events and logs |
| node | the node; its events; `top node`; the pods on it that are not Running |
| namespace | every pod that is not Running and Ready, every Deployment, StatefulSet, DaemonSet, Job whose status disagrees with its spec, every Service without endpoints, every PVC not Bound; then the walk for each, within the budget |

What is gathered beyond the first level is driven by the checks' declared needs (§5): a check asks for
evidence kinds, and the walk fetches them once.

### 4.3 Limits

Logs are cut to the tail (bytes and lines are recorded); lists are capped (500 items, recorded as
`truncated`); a request that fails is kept as evidence of kind `error` with the message, never dropped
silently; the budget stopping the walk is recorded in the case.

## 5. The check catalogue

A check is a plain function `(case: Evidence) -> list[Finding]`, registered in one table with its id,
its one-line meaning, the evidence kinds it needs, and the severity of what it finds. Ids never change
meaning and are never reused (`deprecate-and-migrate`). The first catalogue:

| Id | Fires when | Evidence |
|---|---|---|
| `pod.crashloop.oom` | a container's last termination reason is `OOMKilled` | pod status |
| `pod.crashloop.exit-code` | a container restarts with a non-zero exit code; the finding quotes the last log lines before the exit | pod status, previous logs |
| `pod.crashloop.probe` | restarts caused by a failing liveness probe | pod status, events |
| `pod.probe.failing` | readiness or startup probe failures in events; the finding compares the probe's port and path with the container's ports | events, pod spec |
| `pod.image-pull.not-found` | `ErrImagePull` / `ImagePullBackOff` with a not-found or manifest-unknown message | pod status, events |
| `pod.image-pull.auth` | the same with an unauthorized message; names the `imagePullSecrets` present or missing | pod status, events, pod spec |
| `pod.pending.insufficient-resources` | `FailedScheduling` with insufficient cpu / memory / gpu; compares requests with the nodes' allocatable | events, nodes |
| `pod.pending.affinity` | `FailedScheduling` with node affinity, selector or anti-affinity messages | events, pod spec, nodes |
| `pod.pending.taints` | `FailedScheduling` with untolerated taints; lists the taints | events, nodes |
| `pod.pending.pvc` | the pod waits for an unbound PVC | pod status, PVC |
| `pod.config-ref.missing` | a referenced ConfigMap or Secret (or key) does not exist (`CreateContainerConfigError`) | pod spec, events, the existence of the references |
| `pod.evicted` | the pod was evicted; names the pressure | pod status, node conditions |
| `pod.terminating.stuck` | `deletionTimestamp` older than the grace period; names finalizers | pod |
| `pod.container.waiting` | a container waiting with another reason (`CreateContainerError`, `RunContainerError`) with the message | pod status |
| `deployment.rollout.stuck` | `Progressing` is `False` or `ProgressDeadlineExceeded`; names the new ReplicaSet and its pods' findings | deployment, replicasets |
| `deployment.replicas.unavailable` | available replicas below desired; distinguishes "no pods" from "pods not ready" | deployment, pods |
| `service.no-endpoints` | the Service's selector matches no pod, or matches pods that are not Ready; shows the selector beside the pods' labels | service, endpointslices, pods |
| `service.port-mismatch` | `targetPort` matches no container port of the selected pods | service, pods |
| `ingress.backend-missing` | an Ingress names a Service or port that does not exist | ingress, services |
| `pvc.pending` | a PVC not Bound; names the StorageClass and whether a provisioner exists | pvc, storageclasses, events |
| `node.not-ready` | a node's `Ready` condition is not `True`; quotes the reason | node |
| `node.pressure` | `MemoryPressure`, `DiskPressure` or `PIDPressure` is `True` | node |
| `job.backoff-limit` | a Job failed with `BackoffLimitExceeded`; links the pods' crash findings | job, pods |
| `cronjob.not-scheduling` | a CronJob is suspended, or its last schedule is older than two periods | cronjob |
| `rbac.forbidden` | events or logs show `forbidden` from the API server for a ServiceAccount | events, logs |
| `hpa.cannot-scale` | an HPA's `ScalingActive` or `AbleToScale` is `False`; quotes the reason (missing metrics, at max) | hpa |

Every check has a recorded cluster that makes it fire and one that does not. A check's finding carries
at least one remedy (§8). Checks never call a model.

## 6. Reasoning: the `Reasoner` port

```text
class Reasoner(Protocol):
    model: str
    def reason(self, request: ReasoningRequest) -> ReasoningReply   # one structured call
```

When: after the checks, at most **once per case**, and only when the user did not pass `--no-reason`.
The request holds the symptom, the findings, and a **digest** of the evidence (resource summaries, the
event lines, the log tails), already redacted (§12), within a token budget that is a setting. The reply is
structured: `root_cause` (text, and the finding ids and evidence ids it rests on), `ranking` (finding
ids in order of likely cause), `missing_evidence` (what to read next, as read-only `kubectl` commands),
`remedies` (each named to a finding, with risk). Code checks the reply: every finding and evidence id
must exist; every command must be read-only (the allowed verbs of §4.1), or it is moved to remedies and
marked; at most two repair calls with the list of problems; a reply that still fails is kept as evidence
of kind `error` and the case is complete without it. Everything said by the model has grade `reported`.

Adapters: **`OpenAICompatible`** (core, `urllib` only; base URL, model and key from settings and the
environment), and `hone-models` as the optional extra `models` for the owner's local and hosted models.
**`FakeReasoner`** (`k8sholmes.testing`) replies from a script. Every call is recorded in the case: the
request as sent, the reply, the model, tokens, duration and price when known.

## 7. The case file

```text
cases/<case_id>/                 case_id = <UTC time>-<6 hex of sha256(context, symptom, time)>
  case.json                      format_version "1": symptom, context, started_at, finished_at, budget,
                                 the list of evidence ids, the findings, the root cause, the remedies
  evidence/<id>.json | .log      each item as returned (JSON) or as text (logs), one file each
  evidence/index.json            id, kind, source request, read_at, sha256, bytes, truncated, redactions
  reasoning/<n>-request.json     exactly what was sent to the model (after redaction)
  reasoning/<n>-reply.json       the reply, the checks' verdict on it, model, tokens, duration, price
  report.md                      the report for people (§8)
```

Rules: every file is written to a temporary name and renamed into place; `case.json` is written last and
is the commit marker (a folder without it is an unfinished case); adding optional keys keeps
`format_version`; removing a key or changing its meaning needs a new version and a change record; an
old case opens or is refused with `InvalidCase` saying how to migrate. Cases live under
`$K8SHOLMES_HOME` (default `~/.k8sholmes/cases/`) or `--out`.

## 8. Remedies and the report

The report (`report.md`) reads top to bottom: the symptom and when; the root cause with its grade; the
findings in order of severity, each with its message, the evidence it rests on (by id, with the lines
quoted) and its remedies; the evidence that was read; what the model was asked, if anything; what was not
read (budget, errors, permissions). Remedies are exact commands or manifest changes in fenced blocks,
each marked with its risk and the finding it fixes. k8sholmes never runs a remedy and has no flag that
would.

## 9. The command line

```text
k8sholmes investigate pod/web-7d9f -n shop [--context kind-dev] [--no-reason] [--out DIR] [--json]
k8sholmes investigate deployment/web | service/web | job/import | node/worker-2 | -n shop   # a namespace
k8sholmes checks                            # the catalogue: id, meaning, severity
k8sholmes cases [--out DIR]                 # the cases, newest first
k8sholmes show <case_id>                    # the report of a case
k8sholmes dashboard [--port 8799]           # the local dashboard (§11)
```

Exit codes: 0 when the case was written (whatever it found), 1 on failure (cluster unreachable, invalid
symptom), 2 on usage. `--json` prints `case.json`. Never prompts.

## 10. Python API

```text
import k8sholmes as kh

cluster = kh.Kubectl(context="kind-dev")                # or kh.testing.RecordedCluster(path)
case = kh.investigate("pod/web-7d9f", namespace="shop", cluster=cluster, reasoner=None)
case.findings            # list[Finding], by severity
case.root_cause          # RootCause | None
case.remedies            # list[Remedy]
case.save(dir)           # the folder of §7; returns its path
kh.open_case(path)       # a Case from a folder, no cluster needed
kh.checks()              # the catalogue
```

Modules (planned): `records.py` (the pydantic models of §2 and §7), `ports.py`, `adapters/kubectl.py`,
`adapters/openai_compatible.py`, `adapters/hone_models.py`, `gather.py` (§4), `checks/` (one module per
area, one table), `reason.py` (§6), `case.py` (§7), `report.py` (§8), `cli.py`, `dashboard.py` with
`dashboard_page/`, `testing/`.

## 11. The dashboard

A local server from the standard library, bound to `127.0.0.1` only, serving a static page with no build
step: the cases list; a case view (the symptom, the root cause, the findings with their evidence opened
in place, the remedies with a copy button, the model's request and reply); a form to start an
investigation (context, symptom, namespace, reasoning on or off) with progress streamed as the walk runs.
No authentication, because it listens only on the loopback interface; a different bind address is
refused.

## 12. Privacy and secrets

- The `Cluster` adapter never fetches a Secret's body: `get secret` is refused; a pod's Secret references
  are recorded as `<secret ref: name/key>` from the pod spec alone, with an existence check through
  `auth can-i` and `get secret <name> -o name` only when the user allows it with `--check-secret-names`.
- Before anything is written to the case or sent to a model, the redactor removes: environment variable
  values whose names contain `key`, `token`, `password`, `secret` or `credential`; values that look like
  JWTs, cloud keys or private keys; `Authorization` headers in logs. Every redaction is counted in
  `evidence/index.json`.
- The kubeconfig is never copied; only the context name is recorded.
- What the model saw is exactly `reasoning/<n>-request.json`; `--no-reason` sends nothing anywhere.
- No telemetry, no network except the cluster and the chosen model endpoint.

## 13. Testing

The default suite is offline, fast and deterministic: unit tests with `FakeCluster`, acceptance tests on
recorded clusters, contract tests of every `Cluster` and `Reasoner` adapter against the checkers. Every
check has a recording that fires it and one that does not. `tests/cluster/` (marker `cluster`) runs
against a throwaway kind or minikube cluster with manifests that reproduce each pattern; `tests/hosted/`
(marker `hosted`) makes one real model call. A planted fake token in a recording must appear nowhere in
the case file, the report or the logs.

## 14. Guarantees (acceptance cases)

Each is a test `tests/e2e/test_ac<N>_<slug>.py`, through the public API or CLI only, that fails if the
feature is removed.

1. **Read-only.** The package's source contains no `kubectl` verb outside §4.1, and the adapter refuses
   any other verb and any Secret body.
2. **Checks fire on their recordings.** For every check in the catalogue, its positive recording produces
   a finding with that id and its negative recording does not.
3. **Every finding has evidence.** Every finding's evidence ids exist in the case and were read from the
   cluster or derived from such evidence; a finding without evidence cannot be constructed.
4. **The walk follows the owner chain.** Investigating a pod reads its ReplicaSet and Deployment and
   their events; investigating a Deployment reads its unhealthy pods.
5. **A case without a model is complete.** With `reasoner=None` the case has findings, remedies and a
   report, and `reasoning/` is empty.
6. **One reasoning call, checked.** With a reasoner, at most one call plus two repairs is made per case;
   a reply naming an evidence id that does not exist is repaired, and after two failures the case is still
   written and says so.
7. **Secrets never leave.** A recording with a planted token in an environment variable, a log line and a
   ConfigMap-like value yields a case file, a report and a model request that contain none of it, and the
   redaction count says how many were removed.
8. **The case file opens later.** A saved case reopens from its folder with the same findings, without a
   cluster; a folder without `case.json` is reported as unfinished; a newer `format_version` is refused with
   a message that says how to migrate.
9. **Crash safety.** A process killed while writing a case leaves either a complete case or a folder
   reported as unfinished, never a half-read one.
10. **Remedies are never run.** The source has no code path that runs a remedy; every remedy carries a
    risk and the id of its finding.
11. **The CLI.** `investigate` writes a case and exits 0; `--json` prints `case.json`; an unreachable
    cluster exits 1 with a message that says what to check; `checks` lists the catalogue.
12. **Deterministic.** The same recording investigated twice gives identical findings and the same
    evidence hashes (times aside).
13. **Budget.** A walk that would exceed its request budget stops, records that it did, and the case
    still completes.
14. **The dashboard binds only to the loopback interface**, lists cases and shows a case's report.

## 15. Known limits (0.1)

Metrics (`top`) only when a metrics server exists; no `exec` into pods, so DNS and connectivity from inside
a pod are never tested directly (a remedy may suggest the command); logs are tails; CRDs are read as
opaque JSON and no check knows them; one cluster per case.
