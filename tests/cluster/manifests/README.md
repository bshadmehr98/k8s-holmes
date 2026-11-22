# Manifests for the cluster tests

One folder per scenario group of design §17.2, applied by `tests/cluster/conftest.py` into a fresh
namespace of a **throwaway** local cluster (kind or minikube) and deleted afterwards. The files break
things on purpose: never apply them to a cluster with real workloads.

- `shop/00-workloads.yaml`: the demo shop (`api` OOM-killed under a tiny limit, `web` behind a
  LoadBalancer with no NetworkPolicy, `worker` without probes or limits on a `:latest` tag, `scheduler`
  with one replica and no disruption budget, `legacy-export` whose selector matches no pod).
- `shop/10-api-lower-limit.yaml`: a second revision of `api`, so a change with its cause precedes the
  restarts.
- `shop/20-alert-rules.yaml`: PrometheusRules for `web` and `postgres` only; applied when the CRD exists,
  otherwise alert rules are `cannot_answer` and the tests assert that instead.

Images are public and small (`registry.k8s.io/pause`, `python:3.12-alpine`); the cluster needs to pull them once.
