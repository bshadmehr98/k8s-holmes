---
name: implement-change
description: Build an accepted change record or a small change test-first, then update the docs and verify. Use after plan-change is approved, or directly for fixes, refactors, tests and docs work.
---

# Implement a change

1. Re-read the accepted change record (if any) and the `design/current.md` sections it changes.
2. **Tests first.** Write the tests and watch them fail for the expected reason:
   - each new acceptance case as `tests/e2e/test_ac<N>_<slug>.py`, public API or CLI only, on the demo
     recording or a `FakeCluster`, with a `ScriptedModel`; it must fail if the feature is removed;
   - unit tests for edge cases and each error path; contract tests (`tests/contract/`) if a port or a
     capability changed;
   - a new or changed capability gets a recording that shows what it reads and a test of each coverage
     statement it can make (`real-cluster-tests` says how recordings are made);
   - for a bug: a test that reproduces it (`debug-failure`).
3. **Implement** the smallest code that passes, within CONTRIBUTING.md "Keep it simple" and "Code
   conventions": the module boundaries of `design/current.md` §3; errors are `K8sHolmesError` subclasses
   from `src/k8sholmes/errors.py` with messages that say what to do; `kubectl` runs only from the cluster
   adapter with the read-only verbs; every API route goes through the session and role check.
4. Fast loop while working: `uv run pytest -x -q <paths>`, `uv run ruff check .`, `uv run pyright`.
5. Commit each green step as a small Conventional Commit; the body says why.
6. Run `sync-docs`, then `verify-before-done`.
7. Report to the user: what changed, which tests prove it, anything left open. Then wait for
   "push" / "ship it" / "I'm happy", which starts `open-pr` (or, while the repository is local only, the
   maintainer's merge).
