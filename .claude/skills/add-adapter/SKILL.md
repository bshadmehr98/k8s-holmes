---
name: add-adapter
description: Add an implementation of one of k8sholmes's ports (Cluster, Reasoner) or another optional integration, with its extra, fake, contract test and docs. Use when adding support for a new way to reach a cluster, a new model provider or a new output.
---

# Add an adapter

k8sholmes's ports are `Cluster` (`design/current.md` §3: how evidence is read from a cluster) and
`Reasoner` (§6: the model that reasons over gathered evidence). A new port, or a change to one, is a
design change: `plan-change` first. For a new adapter, follow the pattern:

1. **Where.** Each port is a small `typing.Protocol` owned by k8sholmes, in its ports module.
   Adapters go in the adapters subpackage and are imported lazily.
2. **Optional dependency.** Add an extra in `pyproject.toml`. Import the library only inside the adapter
   module. The core must still import without it: `tests/unit/test_import_boundaries.py`.
3. **Contract.** Every port has a fake and a contract checker in `k8sholmes.testing`; run the checker
   against the adapter in `tests/contract/`.
4. **Read-only.** A `Cluster` adapter uses only the verbs `design/current.md` §4 allows, and redacts
   Secret values before anything leaves it (§12). A `Reasoner` adapter never receives raw Secrets or
   credentials, and records every call (model, tokens, time) in the case file.
5. **Behaviour the port promises.** Write it in `design/current.md` and test it; an adapter that can't
   keep a promise fails clearly instead of pretending.
6. **Tests offline** in the default suite (fakes, recorded clusters); a real cluster or model only in
   `tests/cluster/` or `tests/hosted/` (`real-cluster-tests`).
7. `sync-docs`: the install line in `README.md`, `docs/adapters.md`, an example if it's new.
