# Why k8sholmes exists

> Ask a question about your Kubernetes environment in plain words. K8sHolmes frames it, finds out what it
> may read, chooses from a catalog of investigative capabilities, gathers evidence, tests explanations
> and answers with findings that cite their evidence, say what they could not know, and respect what your
> team has already decided is intentional. Reports are computed from the records. A model you bring does
> the reasoning; code does the deciding.

**Status:** the design ([changes/0001](changes/0001-initial-design.md)) is accepted (2026-11-01, from the
owner's brief in [history/0001-owner-brief.md](history/0001-owner-brief.md)) and being built in
milestones; [current.md](current.md) §19 says what is implemented.

## The problem

Understanding and repairing a Kubernetes environment is a ritual of context gathering that is repeated
every time and recorded nowhere:

- **Context is expensive.** What runs here, what depends on what, who owns it, what changed last week,
  what is exposed: each answer is a different `kubectl` invocation, a dashboard, a wiki page or a
  colleague, in the right order, every time.
- **The same findings come back.** A single-replica scheduler that is single on purpose is "a problem"
  in every review, because the decision that it is intentional lives in someone's head.
- **Conclusions outrun their evidence.** "This service is unused" when nobody measured traffic; "the
  cluster is isolated" after one look at NetworkPolicies; a request path drawn from guesses and presented
  as a trace; a report whose percentages a model made up.
- **AI helpers are either toothless or dangerous.** A model fed a pasted `describe` guesses; a model with
  a shell is unauditable and one call away from a change. Neither records what it saw, respects the team's
  decisions, or says what it could not know.
- **Fixes are not verified.** A change is applied, the alert clears, the incident is closed; whether the
  condition is gone is a separate question nobody asks with evidence.

## The core ideas

1. **One general investigation, many questions.** A question is framed, scoped and timed; the engine
   discovers what it may read, chooses capabilities, gathers evidence, tests explanations, adapts, and
   concludes with findings, limitations and next steps. No question has its own hardcoded path.
2. **Tool execution as the core, gated by code.** Every capability has a purpose, validated inputs, the
   permissions it needs, a class (inspect, probe, change) and an output with coverage. What may run is
   decided by code from the user's role and the environment's settings, never by the model or by text in
   a log.
3. **Evidence with coverage.** Everything read is kept with its source, query, time and hash. Every
   piece says what it can state: observed, observed absent, no data, or cannot answer. The engine refuses
   conclusions the coverage does not support.
4. **Observations, hypotheses, reviewed conclusions.** Findings are separated by kind, cite evidence for
   and against, state uncertainty and missing information, carry no invented confidence numbers, and keep
   model text and human edits apart with their history.
5. **The team's decisions are records.** An accepted exception names one condition on one exact scope,
   with the explanation, the approver, the dates and what should trigger reconsideration; it is applied
   precisely in later investigations and never suppresses anything else. Knowledge notes are kept apart
   from facts and shown where they influenced a conclusion.
6. **Numbers and reports come from records.** Every count is computed; every report is built from stored
   evidence and findings and stays readable when the workload is gone.
7. **Verified means evidence.** Proposals state their intended effect and verification; a verification
   reruns the checks and compares before and after. Completion, alert clearance and recovery are three
   things.
8. **Your model, visibly.** Any OpenAI-compatible endpoint, tested for the capabilities an investigation
   needs, with usage shown and no silent switching.

## Non-goals (0.1)

Applying infrastructure changes (the authorization path and the verification exist; no change capability
ships); running inside the cluster; alert routing; a metrics, logs or tracing backend of its own; fleets
as one object; vulnerability scanning; cost; a hosted service; telemetry.

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as it stands: concepts, rules, the guarantees the tests check, what is implemented |
| [`changes/0001-initial-design.md`](changes/0001-initial-design.md) | the first design, from the owner's brief (accepted), with its milestones |
| [`history/0001-owner-brief.md`](history/0001-owner-brief.md) | the owner's brief, word for word |
| [`history/0000-research.md`](history/0000-research.md) | what exists already (HolmesGPT, k8sgpt), and what is still to research |
| [`decisions.md`](decisions.md) | the owner's answers and the designer's choices, each open to review |

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [changes/](changes/) and [decisions.md](decisions.md); the
process is in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
