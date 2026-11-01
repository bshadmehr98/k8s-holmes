---
name: plan-change
description: Plan a design change as a change record in design/changes/ and get it approved before writing code. Use after start-task when a change touches the public API, CLI, guarantees, capabilities, the engine, records or the schema, coverage rules, exception matching, roles, ports, extras or dependencies.
---

# Plan a design change

1. Read `design/current.md` (§1 goals and non-goals, the sections the change touches, §18 guarantees),
   `design/decisions.md` and the related records in `design/changes/`.
2. Next number: the highest `design/changes/NNNN-*.md` plus one.
3. Write `design/changes/NNNN-<short-name>.md` in the format of the existing records (for example
   `design/changes/0001-initial-design.md`): Status `proposed`, Context, Problem, Options (at least two,
   one of them "leave it as it is"), Decision, Consequences, Migration and compatibility. Include:
   - a sketch of the public API, the CLI or the interface as a user would meet it;
   - the new or changed acceptance cases, numbered after the last one in `design/current.md` §18;
   - the tests that will prove it (unit, e2e on the demo recording, a real cluster if evidence gathering
     changes).
4. Answer each risk the change touches, in the record:
   - **the gate stays code** (§5.2): what may run is decided from the user's role and the environment's
     settings, never from the model or from text in a result; a new capability states its class, systems,
     permissions and coverage;
   - **coverage** (§7): a new capability or question shape says which statements it can make, and the
     engine's validation (§6.3) still refuses conclusions the coverage does not support;
   - **records** (§2, §16): new optional columns or JSON keys keep the schema version; anything else is a
     migration with `deprecate-and-migrate`; ids and condition codes never change meaning;
   - **exceptions and knowledge** (§9): matching stays exact; knowledge is never evidence;
   - **secrets and access** (§14): nothing new reaches a model message, a report, an export or a log that
     the redactor has not seen; every new API route checks the session, the role and the environment;
   - **the core imports no extra**; a new dependency is an optional extra with a reason in
     `design/decisions.md`;
   - is there a smaller option that meets the need? (CONTRIBUTING.md, "Keep it simple")
5. Commit: `docs(design): propose NNNN <short name>`.
6. **Stop and ask.** Give the user the decision and the open questions in a few lines, with the file
   path. Only the project's maintainer accepts a record. If the user is the maintainer and approves, set
   the status to `accepted (approved by the maintainer YYYY-MM-DD)`, commit, and continue with
   `implement-change`. Otherwise the status stays `proposed`: offer to open a pull request with the record
   alone, and open it (`open-pr`) only when the user says so (AGENTS.md rule 10); or link the record from
   the issue. The maintainer decides there. On rejection set `rejected` with the reason and stop.
