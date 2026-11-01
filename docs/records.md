# Records, the schema and the exports

Every record is a pydantic model in `k8sholmes.records` (design §2); the SQLite store keeps each one as
JSON in a table of its own, with a few indexed columns for the queries the code makes (design §16.1).
Ids are short and prefixed by the record's kind (`inv_`, `fnd_`, `ev_`, `rep_`, `prp_`, `sav_`, …); times
are ISO-8601 UTC.

| Record | Table | What it is |
|---|---|---|
| `Environment` | `environments` | a cluster context (or a recording), its settings, its permission matrix |
| `Inventory` | `inventories` | a snapshot of what the environment holds, with relationships and their basis |
| `User`, `Session`, `AuditEntry` | `users`, `sessions`, `audit` | local accounts with roles per environment, sign-ins, the audit log |
| `ModelEndpoint`, `ModelCall`, `CapabilityTestResult` | `model_endpoints`, `model_calls`, … | an OpenAI-compatible endpoint, each call's usage, the capability tests |
| `Investigation`, `Message`, `Step`, `Call`, `Evidence` | `investigations`, `messages`, `steps`, `calls`, `evidence` | a question and everything done to answer it; evidence carries its coverage, redactions and retention |
| `Finding` | `findings` | an observation, hypothesis or conclusion with the evidence for and against it, its state and revisions |
| `AcceptedException`, `Knowledge` | `exceptions`, `knowledge` | what the team accepted as intentional, what the team knows |
| `Report`, `Share` | `reports`, `shares` | a report revision with its sections, numbers and edits; a view-only link (the token's hash only) |
| `Proposal` | `proposals` | a proposed improvement with its state, revisions and verifications |
| `SavedInvestigation` | `saved_investigations` | a question to run again, its schedule, notification and runs |

## Schema versions

The store writes its version in `schema_version` and refuses a file written by a newer K8sHolmes.

| Version | K8sHolmes | Change |
|---|---|---|
| 1 | milestone 1 (0.1.0) | the first tables |
| 2 | milestone 3 (0.1.0) | the tables `reports`, `shares`, `proposals`, `saved_investigations` |

Opening a version 1 file adds the missing tables and writes version 2; nothing else is rewritten.

## Exports

An investigation export (`k8sholmes export`, `GET /api/investigations/{id}/export`) is one JSON object:
`format_version` (`"1"`), `investigation`, `findings`, `steps`, `calls`, `evidence`, each the record as
JSON. The command line also writes `report.md` and one JSON file per piece of evidence.

A report export (`GET /api/reports/{id}/export`) is `format_version` (`"1"`) and `report`: the revision
with its `sections` (each with a `heading`, a `kind` of `text`, `list`, `table` or `findings`, the `body`
and `counted`, the records behind its numbers), `numbers` and `edits`. The Markdown of a report
(`/markdown`) renders the same revision; a person's edits follow their section as quotes with a byline.

Both exports pass through the redactor before storage, so a secret that reached a result is already
`[REDACTED]` here; the redaction count on each piece of evidence says how many were removed.
