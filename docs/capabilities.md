# Capabilities

A capability is one thing K8sHolmes can do to answer a question (design §5): it has a stable id, a purpose
in plain words, validated inputs, the permissions it needs, a class and the coverage statements it can
make. The model chooses capabilities; **code decides what may run** from the user's role and the
environment's settings (§5.2). `kubectl` runs only from the cluster adapter, as an argument list with a
timeout, with the read-only verbs `get`, `logs`, `top`, `auth can-i`, `version` and `api-resources`.
Nothing writes to a cluster in 0.1.

## Classes

| Class | Who may run it | What it does |
|---|---|---|
| `inspect` | investigator | reads the cluster or the store; never sends traffic to a workload |
| `probe` | operator, when the environment enables probes | an active test that sends traffic (one HTTP request) |
| `change` | nobody in 0.1 | would change infrastructure; refused unless the environment's authorization path is set (§11.3), and no built-in exists |

## Coverage

Every result states what it could know: `observed` (it saw the thing), `observed_absent` (it looked where
the thing would be and found nothing), `no_data` (the source kept nothing for the period, such as expired
events) or `cannot_answer` (no source that could know, such as traffic with Kubernetes alone). The engine
turns a conclusion that the coverage does not support into a limitation (§6.3).

## The built-in catalog

| Id | Class | Permissions it needs | Coverage it can state | What it reads |
|---|---|---|---|---|
| `evidence.recall` | inspect | none (reads the store or the K8sHolmes host) | `observed`, `observed_absent` | Findings and evidence of earlier investigations in this environment about a resource, namespace or condition. |
| `http.probe_url` | probe | none (reads the store or the K8sHolmes host) | `observed`, `cannot_answer` | One HTTP request from the K8sHolmes host to a URL: status, latency, TLS expiry. An active probe: it sends traffic. Says nothing about reachability from inside the cluster. |
| `k8s.alert_rules` | inspect | `list prometheusrules`, `list servicemonitors` | `observed`, `cannot_answer` | PrometheusRule alerts and ServiceMonitors in the scope when those CRDs exist, and which workloads no alert references. Whether an alert fired is cannot_answer; without the CRDs everything is. |
| `k8s.changes` | inspect | `list replicasets`, `list deployments`, `list events` | `observed`, `no_data` | Revisions (ReplicaSets with their change cause and images), resources created, rollouts in progress and change events in a namespace since a time. Use for 'what changed before the problem'. |
| `k8s.events` | inspect | `list events` | `observed`, `observed_absent`, `no_data` | Events in a namespace or about one resource, in a period. Events expire after about an hour, so an empty answer for a longer period is no_data, not absence. |
| `k8s.get` | inspect | `get pods` | `observed`, `observed_absent` | One resource as the cluster returns it, with a summary of the fields that matter. Secrets: metadata only, never the body. |
| `k8s.inventory` | inspect | `list pods`, `list deployments`, `list replicasets`, `list statefulsets`, `list daemonsets`, `list jobs`, `list cronjobs`, `list services`, `list endpointslices`, `list ingresses`, `list networkpolicies`, `list persistentvolumeclaims`, `list horizontalpodautoscalers`, `list poddisruptionbudgets`, `list serviceaccounts`, `list configmaps`, `list secrets` | `observed`, `no_data` | What exists in the scope and how it relates: workloads, pods, services, ingresses, policies, volumes, autoscalers, budgets, config names; relationships marked defined, observed or inferred. Start here for questions about a whole namespace. |
| `k8s.list` | inspect | `list pods` | `observed`, `observed_absent` | Resources of one kind in a scope, with a label or field selector; summaries, not bodies. |
| `k8s.logs` | inspect | `get pods/log` | `observed`, `no_data` | The tail of a container's log, current or the previous (crashed) one, since a time or not. |
| `k8s.network_exposure` | inspect | `list services`, `list ingresses`, `list networkpolicies`, `list pods` | `observed`, `cannot_answer` | Services of type LoadBalancer or NodePort, Ingresses and their hosts, NetworkPolicies and which pods they select, pods selected by none, namespaces without policies. Only what the cluster shows; anything beyond it is cannot_answer. |
| `k8s.permissions` | inspect | none (reads the store or the K8sHolmes host) | `observed` | The permission matrix of the environment from the last connection check: which verbs on which kinds the cluster allows. |
| `k8s.safeguards` | inspect | `list deployments`, `list statefulsets`, `list daemonsets`, `list poddisruptionbudgets`, `list horizontalpodautoscalers` | `observed` | Per workload: liveness and readiness probes, requests and limits, replicas, a PodDisruptionBudget, an autoscaler, anti-affinity, :latest tags, token automount; with counts computed by code. The basis of a reliability review. |
| `k8s.service_backends` | inspect | `get pods`, `list pods`, `list endpointslices` | `observed`, `observed_absent`, `cannot_answer` | A Service's selector, the pods it matches and their readiness, its endpoint slices, and whether its target ports match a container port. Traffic is cannot_answer. |
| `k8s.top` | inspect | `list pods.metrics.k8s.io` | `observed`, `cannot_answer` | Current CPU and memory of pods or nodes from the metrics server, when one exists. |
| `k8s.workload_health` | inspect | `get pods`, `list pods`, `list events`, `get pods/log` | `observed`, `observed_absent` | A workload's pods with their conditions, restarts, last terminations and waiting reasons; its owner chain; its events; the events and previous logs of up to three unhealthy pods. Use for 'why is X failing'. |
| `knowledge.search` | inspect | none (reads the store or the K8sHolmes host) | `observed`, `observed_absent` | Knowledge notes (ownership, criticality, traffic expectations, maintenance windows, constraints, dependencies, lessons) and accepted exceptions whose scope touches a resource or namespace. User-provided, never evidence about the cluster. |

Evidence from `k8s.logs` and `k8s.top` is short-lived: the retention policy removes it after the
configured days unless a reviewed finding, a verification or a report cites it (`docs/privacy.md`). Every
other capability's evidence lives as long as its investigation.

## Settings, test and history

Administrators set, per environment and capability: enabled or disabled, a namespace allow-list, and the
caps (`max_items`, `max_log_bytes`, `timeout_ms`). **Test** on the Capabilities page runs a capability with
sample inputs and shows the result and its coverage before it is enabled for investigations. **History**
lists every call with its inputs, status, duration, error, the investigation and the user; a refused call
is a recorded call with its reason, and the audit log carries it too.

## Retired ids

None. An id is never reused: a retired capability keeps its id in this list with the version that
retired it and what replaced it (`.claude/skills/deprecate-and-migrate`).

## Your own

[`extensions.md`](extensions.md): a Python entry point or a command capability declared in TOML.
