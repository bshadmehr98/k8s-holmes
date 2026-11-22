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
  a test refuses to run against a context whose name does not start with `kind-` or `minikube` unless
  `K8SHOLMES_TEST_ALLOW_CONTEXT=1`.
- The cluster's fixtures live in `tests/cluster/manifests/`, one folder per demo scenario of
  `design/current.md` §17.2.

**Run**
- `uv run pytest -m cluster -q` with the cluster up. The hosted test (`-m hosted`, one small real model
  call) needs the key named in CONTRIBUTING.md in the environment.
- Skips with a precise reason when `kubectl` is missing, the context is not a local cluster, or the key
  is unset.

**Record**
- A capability's evidence gathering is proven on a real cluster once, then recorded: the `Kubectl`
  adapter's record mode writes every request and reply (JSON) under `tests/fixtures/clusters/<name>/`.
  The recording is raw: the redactor runs on evidence, not on the recording. The default suite and the demo replay recordings with
  `k8sholmes.testing.RecordedCluster`.
- Recordings are small (one namespace, a few resources) and hand-checked for anything private (hostnames,
  tokens, image registries with credentials) before they are committed.

**Write**
- Put the test in `tests/cluster/` with `@pytest.mark.cluster`. The demo scenarios share one
  module-scoped namespace named `shop` (the demo investigator names it), created by the `shop` fixture
  and deleted afterwards, even on failure; a test of something else applies its manifests into a fresh
  namespace named after the test and deletes it the same way.
- Assert facts that hold for any good investigation: the capability's result has the expected coverage, a
  finding with the expected condition code cites evidence that exists, a limitation is stated where the
  source cannot answer. Never assert exact model text.

**Report** what ran and what was skipped, with reasons, in the branch's summary (and the PR's "How it was
tested" once the repository is on GitHub).
