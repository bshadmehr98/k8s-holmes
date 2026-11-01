# 0000: What exists already, and what is still to research (November 2026)

k8sholmes started on 2026-11-01 from a project name and the owner's request to set the project up in the
shape of their other repositories. No investigation of a real failure has been recorded yet, and no
candidate design has been tried. This note records what was looked up before writing the first design,
with the evidence level of each statement in the style of the owner's research standard (`sourced`: from
a web page read that day; `inferred`: our reasoning, not tested).

## 1. Two open-source tools already do most of this

**HolmesGPT** (`sourced`, read 2026-11-01). An open-source AI troubleshooting agent for Kubernetes and
cloud-native systems, started by Robusta.dev with contributions from Microsoft, now a CNCF sandbox
project, MIT licensed, on PyPI as `holmesgpt`. It combines observability data, LLM reasoning and runbooks;
it has a CLI, a Helm chart, an "operator mode" for background detection, Slack through Robusta, and
integrations for Loki, Kafka and others. It runs read-only investigations and, with read-only RBAC, cannot
see Secrets by default.

**k8sgpt** (`sourced`, read 2026-11-01). An open-source CLI, read-only by design, with built-in
analyzers for pods, PVCs, ReplicaSets, Services, Events and Ingresses and support for custom analyzers;
`k8sgpt analyze --explain` adds an LLM explanation from one of many backends, with optional anonymisation
of the data sent; an in-cluster operator for continuous monitoring; an MCP server for Claude Desktop.

Carried into the design:

- The **name** clashes. "K8sHolmes" and "HolmesGPT" will be confused, and HolmesGPT is a CNCF project.
  This is the first open question for the owner ([decisions.md](../decisions.md) D-001).
- k8sgpt's analyzers resemble the built-in capabilities of design §5, but a fixed analyzer per question is
  what the brief rules out; the design's engine chooses capabilities per question and keeps evidence with
  coverage, knowledge, exceptions and verification as records (`inferred`: that these differences matter
  to users is not tested).
- HolmesGPT is agentic too. The design differs in the code gate on every call, the coverage rules, the
  organizational knowledge and exceptions, and reports computed from records (`inferred`).

## 2. What was not researched

- The owner's brief ([0001-owner-brief.md](0001-owner-brief.md)) arrived later the same day and settled
  what the product is; this note predates it.
- The conditions that matter most in practice, and their frequency. The built-in capabilities and
  condition codes of design §5 and §8 are a first list from common experience; they should be validated
  against real incidents.
- Which model investigates well with tools over redacted cluster evidence, at what cost, and how often it
  cites evidence that does not exist. The design's validation (§6.3) and model comparison (§13.4) make
  this measurable.

## 3. The machine this started on

A Linux laptop with `kubectl` 1.36.2 and Docker installed, one kubeconfig context named `default`, and
no local cluster tool (no kind, minikube or k3d). The cluster tests of the design assume a throwaway
local cluster made with kind or minikube; one of them has to be installed before those tests can run.

## Sources

- HolmesGPT on PyPI: https://pypi.org/project/holmesgpt/
- CNCF blog, "HolmesGPT: agentic troubleshooting built for the cloud native era" (2026-01-07):
  https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/
- Robusta documentation, HolmesGPT main features:
  https://docs.robusta.dev/master/configuration/holmesgpt/main-features.html
- k8sgpt on GitHub: https://github.com/k8sgpt-ai/k8sgpt
