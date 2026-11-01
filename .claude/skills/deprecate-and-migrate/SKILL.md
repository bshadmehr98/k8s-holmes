---
name: deprecate-and-migrate
description: Change or remove public API, CLI flags, capability ids, condition codes, records or the store schema without breaking users - deprecation warnings, a compatibility period, migrations. Use when a change renames, removes or changes the meaning of anything users or their stored data depend on.
---

# Deprecate and migrate

Users and their stored investigations, exceptions and reports outlive any version. Break nothing silently.

**Public API and CLI**
1. Keep the old name working for at least one minor release: it calls the new one and warns with
   `warnings.warn("<old> is deprecated, use <new>; it will be removed in <version>", DeprecationWarning, stacklevel=2)`.
   CLI: the same message on stderr.
2. Docs and examples show only the new form. `CHANGELOG.md`: **Deprecated** now, **Removed** when it goes.
3. Tests: the old form still works and warns; the new form works.

**Capability ids and condition codes** (records, exceptions and reports name them: `k8s.events`,
`replicas.single`, …)
1. An id never changes meaning and is never reused; a removed one keeps its id reserved and is listed as
   retired in `docs/capabilities.md`. An exception on a retired condition still matches nothing new and
   stays inspectable.

**The store and the export** (`design/current.md` §16.1, §10)
1. New optional columns and JSON keys keep `schema_version` and the export's `format_version`. Removing a
   column or key or changing its meaning needs a new version and a change record (`plan-change`) with a
   "Migration and compatibility" section, and a numbered migration that runs at start.
2. An older store migrates forward; a newer store is refused with a message that says which version of
   K8sHolmes opens it. Never guess.
3. Keep a small store and export of the old version in the tests and test both the migration and the
   refusal message.
4. Document the versions and their history in `docs/records.md`.
