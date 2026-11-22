# 0002: Tool calling that small models get right

## Status

`implemented in 0.1.0` (approved by the maintainer 2026-11-22; pieces A, B and C built the same day). Builds on design 0001 as of milestone 4 (merged
2026-11-22, PR #1); section numbers and acceptance cases refer to that version of [design/current.md](../current.md).

## Context

0.1 lets a team bring its own model (§13), and the obvious first choice for a self-hosted, privacy-minded
team is a local one: Ollama, vLLM or llama.cpp on a workstation GPU with 8 to 24 GB. Models that fit there
(4B to 30B parameters, often mixture-of-experts) pass the capability tests of §13.2 but fail real
investigations.

The first real investigation on a real cluster (2026-11-22, `gpt-oss:20b` on Ollama, a k3s cluster, the
question "Is there any service that is actually not used?") ended `failed` after 22 steps:

- 12 of 19 capability calls were refused by input validation. The model sent `label_selector` where
  `k8s.list` takes `selector` (the name every Kubernetes client library uses), `class: "none"`, and
  namespaces that do not exist (`lmx-prod` for `lms-prod`, `labb??`).
- The tool message for a refusal is `{"status": "invalid", "error": "… Extra inputs are not permitted"}`.
  It names the bad field (guarantee 2) but not the right one, so the model guessed again; after a few
  rounds it sent the error object back as the arguments of its next call.
- The framing's `intended_capabilities` were invented names (`list_pvc`, `inspect_pod_network`): the
  framing schema does not restrict them to the catalog.
- The concluding reply was not the JSON the schema asks for, even after the repair.
- Step 22: the model wrote two JSON objects as one tool call's arguments; Ollama could not parse them and
  answered HTTP 500, which ended the investigation.
- 111,615 prompt tokens over 21 model calls: every turn carried all 15 tool schemas and the whole thread.

None of this is a defect in the gate or the coverage rules; they did their job. It is the interface the
engine offers the model: many free-form choices per turn, field names the model has not seen, errors that
do not teach, and a growing context. These are known failure modes of small models with well-known
remedies (constrained decoding, smaller decisions, corrective errors, canonicalisation, deterministic
retrieval with the model reasoning over the result).

## Problem

An investigation with a local model of the size a team can run should gather the evidence it needs and
conclude, or stop with an honest limitation, instead of spending its budget on calls the engine refuses
and failing on a malformed reply. The answer today is "use a stronger model", which for many teams means
sending cluster data to a hosted provider, the thing self-hosting is meant to avoid.

## Options

1. **Leave it as it is** and document that investigations need a strong model. No work; local models stay
   unusable for anything but the demo; the capability tests keep saying "passed" for models that cannot
   investigate.
2. **Only better errors and aliases** (items 2 and 5 below). Small and cheap; helps every model; does not
   stop malformed tool calls (step 22) or the choice among 15 schemas per turn.
3. **One constrained action per turn**: replace native tool calling with one JSON action whose schema is a
   union of every capability's inputs, decoded under the server's grammar. Stops malformed output, but the
   union of 15 schemas is large, slow to compile into a grammar on llama.cpp, and still one big decision.
4. **A library for structured agents** (instructor, outlines, LangChain). Each brings a dependency and a
   framework into the core (rule 1, D-009), and none knows the gate, coverage or the records.
5. **All seven remedies, built into the engine with the standard library** (chosen): small decisions under
   a constrained schema, errors that teach, canonical names, known values from the inventory, code-run
   playbooks for common question shapes, a bounded context and a set temperature.

## Decision

Option 5, as seven parts. Native tool calling stays for models that handle it; every part keeps the gate,
validation and the coverage rules exactly where they are.

### 1. A structured tool mode

An endpoint gets `tool_mode`: `native` (today: tools in the request, the model writes tool calls) or
`structured` (the engine asks for JSON under `response_format` and turns it into the same tool calls).
The default is `auto`: `structured` when the endpoint's structured-reply test passed, `native` otherwise;
the effective mode is shown on the endpoint and recorded on every model call.

In `structured` mode each gathering step is two small decisions (part 4):

```text
choose   schema {"action": "call" | "done", "capability": <enum of available ids>, "why": string}
fill     schema = the chosen capability's input schema (with its known values, part 3)
```

The engine builds a `ToolCall` from `capability` and the filled inputs and runs it through
`Registry.execute` exactly as today: validation, the gate, the recorded call, the evidence. The result is
returned as a context message marked as data (§14.2), since there is no native tool call to answer. A
reply that is not valid JSON is a repair request inside the step (once), then a recorded invalid call; it
is never an HTTP error that ends the investigation. A server that ignores `response_format` (the test
reported `unsupported`) gets the schema as an instruction, as the framing does today.

Structured mode makes one call per step; `calls_per_step` applies to native mode only. A step's two model
calls count as one step against the step budget and as two calls against the token budget.

### 2. Errors that teach, and a stuck model stops

`Registry.validate` keeps naming the field (guarantee 2) and adds what the model needs to fix it:

```text
k8s.list: invalid inputs
- unknown field "label_sel": did you mean "selector"?
- namespace "lmx-prod" is not in this environment: did you mean "lms-prod"?
accepted fields: kind (required, one of pods, services, …), namespace, selector, field_selector
```

Suggestions come from `difflib.get_close_matches` over the accepted fields and the known values. The tool
message for an `invalid` or `refused` call is this plain text, not a JSON object shaped like arguments.
After three consecutive invalid calls the engine stops gathering, adds the limitation "the model could not
form valid inputs for <capability>", and concludes with what it has (`partial`), instead of spending the
remaining budget.

The user is told plainly, not only through the limitation list. The investigation page shows a notice at
the top of the activity and of the report: "The model stopped gathering: it sent invalid inputs for
`k8s.list` three times in a row. The answer below uses only the evidence gathered before that. A stronger
model, or `structured` tool mode, may do better." It links the three refused calls, each with what the
model sent and why it was refused, and offers **Resume** (the next attempt gets a fresh count of three)
and **Change model**. The same text is the first line of `investigate`'s output on the command line, and
the event stream carries it as a `stopped_invalid` event, so the notice appears while the run is live.

### 3. Known values instead of free text

The input schema a model sees is computed per investigation from the environment:

- `kind` fields: an enum of the kinds the cluster serves (from `api-resources` at connect) that the
  capability may read;
- `namespace` fields: an enum of the namespaces the cluster lists (or, when it may not, those of a
  complete inventory of every namespace; D-024), intersected with the
  capability's namespace allow-list (§5.4); above 200 namespaces the field stays a string and part 2's
  suggestions do the work;
- the framing schema's `intended_capabilities`: an enum of the available capability ids.

Validation checks the same values, so the enum is a convenience for the model, not the gate: a namespace
outside the allow-list is still refused by code. A namespace created after the snapshot is reported as
refused with the closest suggestion; the namespaces are listed once per run (D-024).

### 4. Fewer choices per decision

In `structured` mode the choose step sees the available capabilities as one line each (id, title,
purpose) with the framing's intended ones first; the fill step sees exactly one schema. In `native` mode
the tools are offered in the same order, intended ones first; the full list stays available. The choose
step can also answer `done`, which ends gathering as an empty tool-call reply does today.

### 5. The names models expect

An input model may declare aliases for a field (pydantic `validation_alias` with `AliasChoices`), and
`kind` values are canonicalised through the kind table of `cluster.py` (`pod`, `pods`, `po` → `Pod`;
`svc` → `Service`; D-024). The first aliases:

| Canonical | Also accepted |
|---|---|
| `selector` | `label_selector`, `labelSelector`, `labels` |
| `field_selector` | `fieldSelector` |
| `namespace` | `ns` |
| `name` | `resource_name` |

The schema shows canonical names only. The call record keeps the canonical inputs in `inputs` and, when
an alias or a canonical kind was applied, what the model sent in `received`. A field that is neither
canonical nor a declared alias is still refused (guarantee 2).

### 6. Playbooks: code gathers, the model reasons

A **playbook** is a fixed list of capability calls for a common question shape, run by the engine through
the gate before the model's own gathering:

```text
Playbook
  id          "unused"                         stable, like a capability id
  title       "Resources that look unused"
  shape       which §7.2 question shape it serves, or none
  calls       [(capability id, inputs over the framing's scope), …]; may fan out over the inventory
              ("k8s.service_backends for every Service in scope"), capped at 40 calls
  limitation  what it cannot say with the environment's sources ("traffic: no traffic source …")
```

The framing schema gets `playbook`: an enum of the playbook ids plus `none`; the model picks one, code
runs it. Each call is a normal recorded call carrying `playbook: <id>`, refused by the gate like any
other, and counts against the budgets. After the playbook, the model's gathering loop continues with
the results in its thread (it may add calls or answer `done`), then concludes; validation (§6.3) is
unchanged. The playbook's `limitation` is added by code when the environment has no capability that could
answer it, so "unused" without a traffic source always carries `cannot_answer` for traffic.

The first playbooks: `unused` (inventory with relationships; service backends for every Service; workloads
scaled to zero; PVCs bound to no pod; ConfigMaps referenced by no pod), `restarts` (workload health, events,
previous logs, changes in the period), `exposure` (network exposure, safeguards), `what_changed` (changes,
events). Playbooks are built in; extensions of playbooks are not part of this change.

### 7. A bounded context and a set temperature

- An endpoint's `limits` gets optional `context_tokens` (at least 1000). When set, every request (its
  tools and schema counted) shows the newest four results in full and older ones as their summary,
  coverage and evidence id, until the estimate (characters ÷ 4) fits; a request that still does not fit
  adds one limitation; the stored thread and evidence are unchanged (like D-017).
- An endpoint gets optional `temperature` (0 to 2), sent with every call when set. The Settings page suggests 0 to
  0.3 for local models in structured mode.
- `docs/setup.md` says that Ollama truncates a prompt silently beyond its context length
  (`OLLAMA_CONTEXT_LENGTH`), and that `context_tokens` should not exceed it.

### What a user meets

Settings → Models, per endpoint:

```text
Tool mode      ( ) auto: structured, since the structured test passed   ( ) native   ( ) structured
Temperature    [ 0.2 ]   (empty: the server's default)
Context        [ 32000 ] tokens   (empty: no limit; keep it at or below the server's context length)
```

The investigation page shows the playbook that ran ("Playbook: resources that look unused, 23 calls") in
the activity, with its calls attributed to it, and the tool mode next to the model. The CLI's
`investigate --json` carries the same fields. No API route is added; `PATCH /api/models/{id}` accepts the
new fields.

### Modules

`actions.py` (new): the structured mode's schemas, parsing and repair, the choose and fill steps.
`playbooks.py` (new): the playbook model, the built-in playbooks, running one. `capabilities/__init__.py`:
aliases, kind canonicalisation, teaching errors, schemas per environment. `thread.py`: ordering, compaction,
data messages for structured results. `engine.py`: the mode switch and the stuck-model stop; it delegates
the rest, so it does not grow.

### Acceptance cases (added to §18)

25. **Structured mode gathers.** With an endpoint in `structured` mode, a scripted model's choose and fill
    replies produce the same calls and evidence as the same requests in `native` mode; a reply that is not
    JSON is repaired once and then recorded as an invalid call, never a failed investigation.
26. **Errors teach.** An invalid call's tool message names the field, lists the accepted fields and
    suggests the closest field or known value; it is plain text.
27. **A stuck model stops.** Three consecutive invalid calls end gathering with a limitation naming the
    capability; the investigation is `partial`, not `failed`; the investigation page, the report, the
    command line and the event stream show the notice of part 2 with the refused calls.
28. **Known values.** The schema a model sees lists the inventory's namespaces within the allow-list and
    the served kinds; an unknown kind or namespace is refused by validation with a suggestion; a namespace
    outside the allow-list is still refused by the gate; the framing's intended capabilities are
    available ids.
29. **Aliases are recorded.** `label_selector` and `kind: "po"` are accepted and stored as `selector` and
    `Pod` with what was sent in `received`; an undeclared extra field is still refused (guarantee 2).
30. **Playbooks run through the gate.** On the demo, "is anything unused?" runs the `unused` playbook; its
    calls are recorded with the playbook's id; a disabled capability in it is refused and becomes a
    limitation; the conclusion still states `cannot_answer` for traffic (guarantee 4).
31. **The context is bounded.** With `context_tokens` set, every gathering request fits the limit, older
    results appear as summary and evidence id, and the stored thread and evidence are unchanged; a set
    temperature is sent on every call.

### Tests

- Unit: validation messages and suggestions, aliases and `received`, kind canonicalisation, schemas per
  environment (enums, the 200-namespace cut-off, the allow-list), the choose and fill schemas, parsing and
  repair, compaction, the stuck-model counter, playbook fan-out and its cap.
- Contract: the OpenAI-compatible client sends `temperature` and the action schemas under
  `response_format`; `ScriptedModel` answers choose and fill steps.
- Acceptance: `tests/e2e/test_ac25` to `test_ac31` on the demo recording. The demo's offline
  investigator is rule-based and stays native; guarantee 25 compares a structured run with a native one
  call by call instead.
- By hand: `tests/cluster/` rerun, since evidence gathering changes; a new `tests/hosted/` case runs the
  demo questions against a configured local model in `structured` mode and reports how many calls were
  valid and whether it concluded. It measures; it does not assert model quality.

## Risks answered

- **The gate stays code.** Enums, ordering and playbooks only shape what the model is offered; every call,
  from the model or a playbook, goes through `Registry.execute` with the user's role and the
  environment's settings. A playbook adds no capability and no permission; choosing a playbook is the
  model choosing a list of calls the gate then judges one by one.
- **Coverage.** Playbooks state their coverage through the capabilities they call, and add their
  limitation by code when no source could answer; §6.3 validation is unchanged and still turns "unused"
  without traffic into a limitation.
- **Records.** New optional JSON keys only: `ModelEndpoint.tool_mode`, `.temperature`,
  `.limits.context_tokens`; `ModelCall.mode`; `Call.received`; `Call.playbook`;
  `Investigation.playbook`. The schema version stays. No id or condition code changes meaning; playbook
  ids are stable like capability ids.
- **Exceptions and knowledge.** Untouched; playbooks call `knowledge.search` like any capability, and
  notes stay influence, not evidence.
- **Secrets and access.** Schemas built from the inventory carry namespace and kind names, which already
  reach the model as evidence; they pass through the redactor like every model message. No new route;
  `PATCH /api/models/{id}` is admin-only as today. Temperature and context settings are not secrets.
- **The core imports no extra.** `difflib` and `json` are the standard library; no dependency is added.
- **A smaller option?** Option 2 is the smaller one and is the first piece built (below). It does not stop
  malformed tool calls or the 15-schema decision, so on its own it does not meet the problem.

## Consequences

- Local models of 4B to 30B become usable for investigations: they choose among short lists, fill one
  schema at a time, cannot write malformed JSON where the server constrains decoding, and are told how to
  fix a mistake. Strong hosted models lose nothing: `native` mode stays, and parts 2, 3 and 5 help them too.
- Structured mode makes two model calls per capability call and one capability call per step:
  investigations take more calls, each much smaller. Budgets keep their meaning.
- Playbooks make the common questions repeatable and cheaper, and move part of gathering from the model to
  code, in line with 0001's "code decides first, the model reasons over what code gathered". They are a new
  catalog to maintain; four to start.
- `engine.py` (598 lines) is already over the module guideline; this change puts the new work in two new
  modules and keeps the engine's growth to the mode switch.

## Migration and compatibility

Existing endpoints get `tool_mode: auto`, which resolves to `structured` if their last structured test
passed; an admin can pin `native`. Existing investigations, calls and evidence read as before; the new
keys are absent. Accepting aliases is wider, not narrower: every input valid today stays valid. The
public Python API gains the `Playbook` model and `tool_mode` on `ModelEndpoint`; nothing is removed.

Build order, each piece its own branch with its own checks:

| Piece | Parts | Guarantees |
|---|---|---|
| A: inputs | 2, 3, 5 | 26, 27, 28, 29 |
| B: structured mode | 1, 4, 7 | 25, 31 |
| C: playbooks | 6 | 30 |

## Open questions for the maintainer

Answered by the maintainer on 2026-11-22:

1. **The default mode:** `auto`, as proposed.
2. **Two steps or one:** two steps, choose then fill, as proposed.
4. **The stuck-model limit:** three consecutive invalid calls, and the user must see clearly why gathering
   stopped and what to do next (the notice in part 2).

3. **The first playbooks:** the four proposed (`unused`, `restarts`, `exposure`, `what_changed`).
5. **Order:** pieces A, B, C in turn, after milestone 4 (merged 2026-11-22).
