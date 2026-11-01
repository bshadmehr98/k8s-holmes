---
name: start-task
description: Start any new piece of work (feature, fix, docs, refactor) on its own branch and decide whether it needs a change record. Use as soon as the user asks for a change, before editing any file.
---

# Start a task

1. `git status`. If there are uncommitted changes that don't belong to this task, stop and ask the user
   what to do with them.
2. `git switch main && git pull --ff-only` (skip the pull while the repository has no remote).
3. `git switch -c <type>/<short-slug>`, named as in CONTRIBUTING.md "Branches and pull requests"
   (`feat/saved-investigations`).
4. Classify the task:
   - **Design change**: it changes the public API or the CLI, a guarantee in `design/current.md` §18, a
     capability's inputs, class or coverage or adds one (§5), the engine's phases, validation or control
     (§6), a record or the store schema (§2, §16), a coverage rule (§7), how exceptions match (§9), roles
     or what is audited (§14), a port (`Cluster`, `Model`), or adds an extra or a dependency. Next:
     `plan-change`.
   - **Small change**: a fix that restores documented behaviour, docs, tests, a refactor, the interface's
     look within the tokens. Next: `implement-change`; a judgment call goes into `design/decisions.md`.
5. Tell the user in one or two lines: the branch, the classification, the next step.

The whole flow is in `CLAUDE.md`.
