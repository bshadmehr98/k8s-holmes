---
name: sync-docs
description: Update every file a change affects - design/current.md, docs/, README, examples, CHANGELOG, AGENTS.md, the change record. Use after implementing any change and before verify-before-done; reviewers use the same table to find missing updates.
---

# Keep the docs in sync

Look at `git diff main...HEAD --stat` and the diff itself, then apply every row that matches:

| If the change... | Update |
|---|---|
| changes behaviour, the public API or the interface | `design/current.md` (the section, §18 guarantees, §19 implemented and planned), the matching `docs/*.md` page, `README.md` if the quickstart or feature list changes |
| adds or changes a CLI command or flag | `docs/cli.md` (its bash blocks run in the tests), `design/current.md` §15.5 |
| adds, removes or changes a capability or a condition code | `design/current.md` §5.3 or §8.1, `docs/capabilities.md`, a recording for it in the tests |
| changes what is read from the cluster or what a model receives | `design/current.md` §5.3 or §6.2, `docs/privacy.md` |
| adds or changes a record, a column or an export key | `docs/records.md`, `design/current.md` §2 and §16 |
| adds an extra or a dependency | `pyproject.toml`, the install section of `README.md`, `docs/setup.md`, the reason in `design/decisions.md` |
| changes how extensions are written | `docs/extensions.md`, `design/current.md` §5.5 |
| changes the demo | `docs/demo.md`, `design/current.md` §17.2 |
| adds a concept users should know | a new example (`add-example`) |
| adds, moves or renames a module | the "Layout" of `AGENTS.md`, `design/current.md` §3 |
| is visible to users | `CHANGELOG.md`, top (unreleased) section: Added / Changed / Deprecated / Removed / Fixed, linking the change record |
| completes a milestone or a change record | `design/current.md` §19; the record's status: `implemented in <next version>` when it is whole |
| is a judgment call without a record | `design/decisions.md`, next `D-` number |
| changes a rule or command contributors use | `CONTRIBUTING.md`, `AGENTS.md`, and the skill in `.claude/skills/` that describes it |

Rules:
- Every `python` block in `README.md` and `docs/*.md` runs in `tests/e2e/test_docs.py`; blocks that must
  not run use a `text` or `yaml` fence. Relative links must resolve (same test).
- Write for users of the product: plain words, the words of `design/current.md` §2, the style of the page
  around it, no internal notes.
- Run `uv run pytest tests/e2e -q` when done.
