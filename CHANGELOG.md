# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning. Why the
design changed is in [`design/changes/`](design/changes/).

## [Unreleased]

### Added

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
