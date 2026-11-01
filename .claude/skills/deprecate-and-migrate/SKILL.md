---
name: deprecate-and-migrate
description: Change or remove public API, CLI flags, check ids or the case file format without breaking users - deprecation warnings, a compatibility period, migrations. Use when a change renames, removes or changes the meaning of anything users or their saved case files depend on.
---

# Deprecate and migrate

Users and their saved case files outlive any version. Break nothing silently.

**Public API and CLI**
1. Keep the old name working for at least one minor release: it calls the new one and warns with
   `warnings.warn("<old> is deprecated, use <new>; it will be removed in <version>", DeprecationWarning, stacklevel=2)`.
   CLI: the same message on stderr.
2. Docs and examples show only the new form. `CHANGELOG.md`: **Deprecated** now, **Removed** when it goes.
3. Tests: the old form still works and warns; the new form works.

**Check ids and finding codes** (case files and reports name them: `pod.crashloop.oom`,
`service.no-endpoints`, …)
1. An id never changes meaning and is never reused; a removed one keeps its id reserved and is listed as
   retired in `docs/checks.md`.

**The case file** (anything k8sholmes writes that a later version reads, `design/current.md` §7)
1. New optional keys are fine and keep `format_version`. Removing a key or changing its meaning needs a new
   format version and a change record (`plan-change`) with a "Migration and compatibility" section.
2. An old case file still opens, or is refused with `InvalidCase` saying how to migrate. Never guess.
3. Keep a small sample of the old format in the tests and test both reading it and the refusal message.
4. Document the format and its version history in `docs/case-file.md`.
