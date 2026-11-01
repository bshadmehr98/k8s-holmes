---
name: real-cluster-tests
description: Run or write the marked tests that need a real local Kubernetes cluster (tests/cluster, marker cluster) or a real model call (marker hosted), and record a cluster's evidence into a fixture the default suite replays. Use before running or changing anything under tests/cluster or tests/hosted, or when a check needs a new recorded cluster.
---

# Real-cluster tests

Details: CONTRIBUTING.md, "Cluster tests". The default suite replays **recorded clusters**; these tests
use a real one.

**The cluster**
- A throwaway local cluster: `kind create cluster --name k8sholmes` (or minikube). Never a cluster with
  real workloads: the tests create broken deployments on purpose (a bad image, an OOM limit, a failing
  probe, a Service whose selector matches nothing). `KUBECONFIG` and `K8SHOLMES_TEST_CONTEXT` choose it;
  a test refuses to run against a context whose name does not contain `kind-` or `minikube` unless
  `K8SHOLMES_TEST_ALLOW_CONTEXT=1`.
- The cluster's fixtures live in `tests/cluster/manifests/`, one folder per failure pattern.

**Run**
- `uv run pytest -m cluster -q` with the cluster up. The hosted test (`-m hosted`, one small real model
  call) needs the key named in CONTRIBUTING.md in the environment.
- Skips with a precise reason when `kubectl` is missing, the context is not a local cluster, or the key
  is unset.

**Record**
- A check's evidence gathering is proven on a real cluster once, then recorded: the adapter's record mode
  writes every `kubectl` request and reply (JSON) under `tests/fixtures/clusters/<name>/`, Secrets
  already redacted. The default suite replays the recording with `k8sholmes.testing.RecordedCluster`.
- Recordings are small (one namespace, a few resources) and hand-checked for anything private (hostnames,
  tokens, image registries with credentials) before they are committed.

**Write**
- Put the test in `tests/cluster/` with `@pytest.mark.cluster`; apply the manifests into a fresh
  namespace named after the test and delete that namespace afterwards, even on failure.
- Assert facts that hold for any good investigation: the check fires with the right id, the finding names
  the evidence, the remedy is a command the user could run. Never assert exact model text.

**Report** what ran and what was skipped, with reasons, in the branch's summary (and the PR's "How it was
tested" once the repository is on GitHub).
