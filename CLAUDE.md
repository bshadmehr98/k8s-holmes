@AGENTS.md

## Claude Code

Every task follows one flow. Each step is a skill in `.claude/skills/`:

```text
new task      -> start-task (branch) -> design change? plan-change, then wait for approval
              -> implement-change (tests first) -> sync-docs -> verify-before-done
done          -> the maintainer merges the branch into main (local git only for now)
```

The GitHub part of the flow is kept for when the repository is published (it is local git only today):

```text
"push" / "ship it" / "I'm happy"
              -> open-pr -> claude[bot] reviews it on GitHub
answers on GitHub -> address-review -> push -> claude[bot] reviews the new commits
merged        -> finish-task
```

- Never edit on `main`: `.claude/hooks/guard-main.sh` blocks the Edit/Write tools on this repository's
  files there (`HOLMES_ALLOW_MAIN=1` overrides it). Shell edits and commits are not blocked, so start
  every task with `start-task`.
- Once published, reviews run only on GitHub: `.github/workflows/claude-review.yml` has claude[bot]
  follow `.github/claude-review.md` with the reviewer agents on every push to a pull request. A branch
  pushed without a pull request gets a reminder from `.claude/hooks/after-push.sh`.
- More skills: `debug-failure`, `add-example`, `add-adapter`, `deprecate-and-migrate`,
  `real-cluster-tests`, `triage-issue`, `learn-from-reviews`, and `release` (only when the maintainer asks).
- Reviewer agents in `.claude/agents/`: `pr-reviewer`, `test-auditor`, `simplicity-reviewer`. Until
  the GitHub flow exists, run them locally on a finished branch (give each the branch's commit range).
- Recommended plugins, enabled in `.claude/settings.json`; install them once with
  `/plugin install pyright-lsp@claude-plugins-official` and
  `/plugin install pr-review-toolkit@claude-plugins-official`.
- `.claude/settings.json` (JSON, so it can't hold comments): `uv run *` and the read-only `kubectl`
  verbs are allowed without asking; every `kubectl` verb that changes a cluster is denied, as are
  `uv publish` and `twine`. The deny rules are a reminder, not a lock: `uv run` can start anything. To be
  asked before commands run, override `permissions` in `.claude/settings.local.json`.
- "The user" in the skills is the person in the session; "the maintainer" is whoever maintains
  k8sholmes. Only the maintainer accepts change records, merges and releases.
- Settings for your machine (`KUBECONFIG`, `K8SHOLMES_TEST_CONTEXT`, a model key for hosted tests, extra
  permissions) go in `.claude/settings.local.json`, which is not committed.
