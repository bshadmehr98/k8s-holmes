# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning. Why the
design changed is in [`design/changes/`](design/changes/).

## [Unreleased]

### Removed

- The error `InvalidCase` of the unreleased skeleton (there are no case files in the accepted design).

### Added

- **Milestone 3: reports and follow-through** ([0001](design/changes/0001-initial-design.md)). Reports are
  built by code from stored records: the live summary, the investigation report, the incident review, the
  reliability and monitoring review and the comparison of two runs; every number is a count of the records
  it names, a regeneration is a new revision, a person's edits keep their byline apart from the generated
  text, downloads are Markdown and JSON, and a view-only share link opens one report without signing in
  until it expires or is revoked. Proposals are made from findings, moved through their states by people
  (K8sHolmes never applies one) and verified by rerunning their capabilities: `verified` only when the
  condition is observed absent, with the evidence before and after side by side. Saved investigations run by
  hand or on a schedule from the server, each run compared with the one before by finding key (new, resolved,
  changed evidence, unchanged counted), a one-line result when nothing changed, and a webhook notification
  carrying the summary and a link. The retention purge runs nightly and removes only what the policy says,
  never anything pinned or referenced, writing what it removed to the audit log. A fixed-evidence model
  comparison concludes with other endpoints on an investigation's stored evidence.
- **Milestone 2: the web interface** ([0001](design/changes/0001-initial-design.md)). `k8sholmes serve`
  runs the interface on the loopback interface: ask a question, watch the steps and calls arrive, read the
  answer first and the findings with their evidence in an inspector, accept a finding as intentional,
  follow up, cancel and resume; browse, scope and test capabilities and read their history; review, edit
  and revoke exceptions and knowledge notes; administer environments, model endpoints (with the capability
  tests), users and grants, retention and the audit log. Every API route checks the session, the role and
  the environment; the first administrator is created from the one-time setup link the server prints.
- **Milestone 1: the engine on records** ([0001](design/changes/0001-initial-design.md)). Investigations
  run from the command line against a Kubernetes environment (through `kubectl`, read-only) or the recorded
  demo: a model you bring (any OpenAI-compatible endpoint, tested for tool calling, streaming and structured
  replies) frames the question, chooses from sixteen built-in capabilities behind a code gate, gathers
  evidence with coverage, and concludes with findings that must cite evidence; accepted exceptions and
  knowledge notes are applied and shown; every step is persisted, and an investigation can be cancelled,
  resumed and followed up. `k8sholmes demo` sets up the demo environment and its offline investigator;
  `investigate`, `capabilities`, `environments` and `export` work; `serve` arrives with milestone 2.
- The design of K8sHolmes, an infrastructure investigation platform, accepted from the owner's brief
  ([0001](design/changes/0001-initial-design.md)); the repository in the shape of the owner's other
  projects: the contributor rules, the Claude Code workflow (skills, hooks, reviewer agents), the GitHub
  workflows, `scripts/check.sh`, and the package skeleton with its typed errors. Nothing of the design is
  implemented yet.
