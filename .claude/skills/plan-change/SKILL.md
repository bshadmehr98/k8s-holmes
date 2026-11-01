---
name: plan-change
description: Plan a design change as a change record in design/changes/ and get it approved before writing code. Use after start-task when a change touches the public API, CLI, guarantees, the case file format, the checks, the ports, what is read from the cluster, extras or dependencies.
---

# Plan a design change

1. Read `design/current.md` (§1 goals and non-goals, the sections the change touches, §14 guarantees),
   `design/decisions.md` and the related records in `design/changes/`.
2. Next number: the highest `design/changes/NNNN-*.md` plus one.
3. Write `design/changes/NNNN-<short-name>.md` in the format of the existing records (for example
   `design/changes/0001-initial-design.md`): Status `proposed`, Context, Problem, Options (at least two,
   one of them "leave it as it is"), Decision, Consequences, Migration and compatibility. Include:
   - a sketch of the public API or CLI as a user would call it;
   - the new or changed acceptance cases, numbered after the last one in `design/current.md` §14;
   - the tests that will prove it (unit, e2e, a recorded cluster if the evidence gathering changes).
4. Answer each risk the change touches, in the record:
   - **read-only**: nothing new writes to the cluster, and every `kubectl` verb used is in the allowed
     list of `design/current.md` §4;
   - **code decides first**: a new model call is justified, whole-case, and its reply is checked by code
     (§6); never one call per pod or per resource;
   - **evidence**: every finding still names the evidence it rests on, with its grade (§2, §5);
   - **case file**: optional keys keep `format_version`; anything else needs `deprecate-and-migrate` (§7);
   - **secrets**: Secret values, tokens and kubeconfig credentials never reach a case file, a report or a
     model (§12);
   - the core imports no extra; a new dependency is an optional extra with a reason in
     `design/decisions.md`;
   - is there a smaller option that meets the need? (CONTRIBUTING.md, "Keep it simple")
5. Commit: `docs(design): propose NNNN <short name>`.
6. **Stop and ask.** Give the user the decision and the open questions in a few lines, with the file
   path. Only the project's maintainer accepts a record. If the user is the maintainer and approves, set
   the status to `accepted (approved by the maintainer YYYY-MM-DD)`, commit, and continue with
   `implement-change`. Otherwise the status stays `proposed`: offer to open a pull request with the record
   alone, and open it (`open-pr`) only when the user says so (AGENTS.md rule 10); or link the record from
   the issue. The maintainer decides there. On rejection set `rejected` with the reason and stop.
