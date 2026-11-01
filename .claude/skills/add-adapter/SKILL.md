---
name: add-adapter
description: Add an implementation of one of K8sHolmes's ports (Cluster, Model) or a new capability, with its fake, contract test, recording and docs. Use when adding support for a new way to reach a cluster, a new model API, a new source, or a new investigative capability.
---

# Add an adapter or a capability

K8sHolmes's ports are `Cluster` (`design/current.md` §5.3: how a cluster is read) and `Model` (§13.1: the
chat completions API). Capabilities (§5.1) are the way a new source joins. A new port, or a change to one,
and any new capability are design changes: `plan-change` first. Then:

1. **Where.** Ports are small `typing.Protocol`s owned by K8sHolmes; adapters live beside them and are
   imported lazily when they need an extra. Built-in capabilities live in the capabilities subpackage, one
   module per system.
2. **Optional dependency.** Add an extra in `pyproject.toml`. Import the library only inside the adapter
   module. The core must still import without it: `tests/unit/test_import_boundaries.py`.
3. **Contract.** Every port has a fake and a contract checker in `k8sholmes.testing`; run the checker
   against the adapter in `tests/contract/`. A capability passes the registry's checks: a stable id, a
   class, systems, permissions, a pydantic input model with descriptions, deterministic `run`, a coverage
   statement on every result.
4. **Read-only and redacted.** A `Cluster` adapter uses only the verbs of `design/current.md` §5.3 and
   never fetches a Secret body; every result passes the redactor before storage (§14.3). A `Model` adapter
   never receives credentials in messages and records usage on every call.
5. **Behaviour the port promises.** Write it in `design/current.md` and test it; an adapter that can't
   keep a promise fails clearly instead of pretending.
6. **Tests offline** in the default suite (fakes, recordings); a real cluster or model only in
   `tests/cluster/` or `tests/hosted/` (`real-cluster-tests`).
7. `sync-docs`: `docs/capabilities.md` or `docs/setup.md`, the install line in `README.md`, an example if
   it's new.
