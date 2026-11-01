# 0001: The owner's brief (2026-11-01)

The product brief the owner gave on 2026-11-01, kept word for word. Design
[0001](../changes/0001-initial-design.md) and [current.md](../current.md) are written from it; where they
read differently, this brief is what the owner asked for and the design says why it departs.

---

Build **K8sHolmes**, an open-source, self-hosted infrastructure investigation platform implemented in **Python**.

K8sHolmes helps engineers understand their Kubernetes environments, investigate problems, discover operational blind spots, and improve reliability. It combines conversational investigation, controlled tool execution, persistent organizational knowledge, and clear evidence-backed reports in a polished web interface.

The product must be powerful enough for experienced engineers while remaining approachable and visually calm. Complexity should become visible when users need it.

### 1. Product goals

K8sHolmes should help users:

- Understand what exists in their environment and how components relate.
- Investigate incidents and unexpected behavior.
- Discover unused resources, configuration problems, and missing operational safeguards.
- Identify gaps in monitoring and alerting.
- Understand relevant changes before a problem appeared.
- Review network isolation and external dependencies.
- Preserve team knowledge and intentional exceptions.
- Propose improvements and verify whether changes worked.

Success means reducing the effort required to gather context, investigate a question, explain a conclusion, and prevent repeated problems.

### 2. Open-ended investigations

Users should be able to ask questions naturally, including:

- Which services appear unused or obsolete?
- Which important alerts are missing?
- What reliability or security practices are we overlooking?
- What problems occurred without triggering an alert?
- Is this environment isolated from external networks?
- Can you reconstruct the likely path of this failure without distributed tracing?
- What changed before this problem started?
- What could be affected if this component fails?
- Did the latest change resolve the issue?

These examples must use a general investigation capability rather than separate hardcoded implementations for every question.

An investigation should:

1. Establish the question, scope, and relevant time period.
2. Discover available information and permissions.
3. Select appropriate investigative capabilities.
4. Gather evidence and test possible explanations.
5. Adapt when evidence contradicts an assumption.
6. Produce findings, limitations, and useful next steps.
7. Continue when a user adds context or requests another check.

Users must be able to cancel, redirect, resume, and revisit investigations.

### 3. Tool execution as a core capability

Tool calling is central to the product.

Provide a discoverable catalog of investigative capabilities. Each capability must have a clear purpose, validated inputs, defined permissions, predictable outputs, and understandable failure behavior.

Users should be able to:

- Browse and search available capabilities.
- Understand which systems each capability accesses.
- Configure its availability and scope.
- Test it before enabling it.
- Inspect execution history.
- Open a call to see its parameters, results, duration, and errors.
- Add custom capabilities through a documented extension mechanism.

The investigation engine should select capabilities according to the question and evidence. It must handle missing permissions, unavailable sources, invalid requests, and incomplete results without silently presenting an investigation as complete.

Separate passive inspection from active probes and infrastructure changes. Enforce permissions outside model-generated instructions.

### 4. Environment understanding and evidence coverage

Maintain an understandable inventory of relevant workloads, services, dependencies, owners, configuration, and connected information sources.

Relationships must distinguish:

- Relationships explicitly defined in configuration.
- Relationships observed in operational data.
- Relationships inferred from incomplete evidence.

Track what information is available, its freshness, and its historical coverage.

The system must distinguish between:

- No activity was observed.
- Activity was observed to be absent within a defined scope.
- The available information cannot answer the question.

It must not label a service unused merely because traffic data is missing, claim complete network isolation from limited checks, or present a reconstructed request path as a verified trace.

### 5. Findings and reports

Every finding should include:

- A concise title and explanation.
- Affected resources and environment.
- Scope and observation period.
- Supporting evidence.
- Contradicting evidence, when present.
- Uncertainty and missing information.
- Recommended next steps.
- Ownership and review status where applicable.

Separate observations, hypotheses, and reviewed conclusions. Avoid unsupported confidence percentages.

Preserve relevant evidence with its source, collection time, query context, and retention policy. Reports should remain understandable when the original workload or external evidence is no longer available.

Provide:

- Live investigation summaries.
- Final investigation reports.
- Incident reviews.
- Reliability and monitoring reviews.
- Comparisons between investigation runs.
- Shareable, permission-controlled views and downloadable reports.

Reports must use consistent structured layouts. Calculate numerical summaries from recorded data rather than asking a model to invent or estimate them.

Human edits, model-generated findings, and report revisions must remain distinguishable.

### 6. Persistent knowledge and accepted exceptions

Users must be able to explain that a finding is intentional and have that decision respected in future investigations.

For example:

> "This service intentionally has one replica because it runs a singleton scheduler, and downtime is acceptable."

Provide an explicit action to accept a finding as intentional. Store:

- The specific condition being accepted.
- Its precise resource and environment scope.
- The explanation.
- The approving user and date.
- An optional review or expiration date.
- Relevant conditions that should trigger reconsideration.
- Revision and revocation history.

Future investigations should recognize applicable exceptions and avoid repeatedly presenting the same accepted condition as a new problem.

Accepted findings must remain inspectable. Exceptions must not suppress unrelated problems or silently apply to similar resources.

Provide a dedicated interface for reviewing, editing, expiring, and revoking exceptions.

Also preserve scoped operational knowledge, such as:

- Expected traffic patterns.
- Service criticality and ownership.
- Maintenance windows.
- Architectural constraints.
- Known dependencies.
- Reviewed lessons from previous investigations.

Distinguish user-provided knowledge from observed facts. Show when stored knowledge materially influenced a conclusion.

### 7. Collaboration and follow-through

Users should be able to challenge findings, add evidence, correct assumptions, assign ownership, and continue another engineer's investigation.

Track findings through clear states such as open, acknowledged, accepted risk, fixed, and verified.

Generate concrete improvement proposals where supported by evidence, including configuration changes, monitoring improvements, and follow-up tasks. Explain their intended effect and verification criteria.

Infrastructure changes must require an explicitly configured authorization path.

After an intervention, repeat relevant checks and compare before-and-after evidence. Keep investigation completion, alert clearance, and verified recovery as separate concepts.

### 8. Saved and recurring investigations

Allow users to save useful questions with their scope and investigation settings.

Saved investigations should support manual reruns and optional schedules. Subsequent results should highlight meaningful changes rather than repeat unchanged recommendations.

Respect accepted exceptions, group duplicate findings, and provide controls over notification frequency.

A recurring investigation with no meaningful changes should produce a concise result.

### 9. Bring-your-own-model support

Users must be able to configure their own OpenAI-compatible model endpoints, including private deployments.

Provide clear configuration and capability testing for:

- Endpoint connectivity and authentication.
- Model selection.
- Tool calling.
- Streaming where supported.
- Structured responses where supported.
- Request limits, timeouts, and usage budgets.

A successful connection does not prove that a model can conduct reliable tool-based investigations. Test the relevant capabilities and explain limitations.

Make model selection and usage visible. Never silently send operational data to an alternative endpoint.

Support comparing models against preserved, reviewed investigation cases. Distinguish fixed-evidence comparisons from evaluations in which models choose their own investigative actions.

### 10. User experience

The primary entry point should invite the user to ask:

**"What would you like to investigate?"**

Offer useful starting questions without limiting free-form input.

Keep the main navigation small and predictable. Organize the product around investigations, capabilities, reports, organizational knowledge, and configuration.

An investigation should initially show:

- The question and scope.
- Current status.
- The most useful finding or answer.
- The next meaningful action.

Evidence, execution details, parameters, raw responses, and historical revisions should open through clear tabs, expandable sections, or contextual inspectors.

Avoid simultaneously displaying every available detail.

Include thoughtful loading, empty, interrupted, partial-success, permission-denied, and error states. Preserve user input and investigation context when operations fail.

Support keyboard navigation, visible focus states, accessible contrast, and layouts that remain usable at different viewport sizes.

### 11. Brand identity and visual design

Use **K8sHolmes** consistently throughout the product.

The visual direction is precise, calm, and technically sophisticated:

- Graphite backgrounds and subtly differentiated surfaces.
- Warm ivory primary text.
- Readable neutral secondary text.
- Restrained copper accents for primary actions and selection.
- Sage green for successful or verified states.
- Amber for review or uncertainty.
- Red reserved for meaningful failure or severity.

Use a distinctive geometric H mark built from bracket-like forms and a small central diamond.

Establish reusable design tokens for colors, typography, spacing, borders, radii, and interaction states.

Use compact, readable typography with clear hierarchy. Reserve monospace for identifiers, parameters, and technical output.

Prefer alignment, whitespace, and subtle separators over excessive cards and borders. Avoid decorative gradients, glowing effects, oversized headings, dense badge collections, and charts without a clear purpose.

Selected details should receive visual emphasis while surrounding context remains quiet.

### 12. Architecture and code organization

**Python is the required implementation language. No particular framework, database, or supporting product is prescribed.**

Use the simplest architecture that supports the required behavior reliably.

Organize code by clear responsibilities, with explicit boundaries between:

- Investigation behavior.
- Tool definitions and execution.
- Evidence and findings.
- Organizational knowledge and exceptions.
- Reporting.
- Model communication.
- Persistence.
- Authorization.
- User interface delivery.

These are responsibility boundaries, not a requirement to create separate services or elaborate frameworks.

Keep domain behavior independent of presentation and external integrations. Isolate external dependencies behind small interfaces where that separation has a concrete purpose.

Avoid speculative abstractions, unnecessary inheritance, redundant wrappers, duplicate representations, and configuration options without a demonstrated use.

Persist important investigation state so interruptions do not lose completed work. Make retries safe and distinguish retryable failures from permanent ones.

### 13. Coding standards

Write code that another engineer can understand without reconstructing the author's intent.

- Use precise, consistent names based on product concepts.
- Prefer small, cohesive functions and modules.
- Keep control flow straightforward.
- Use meaningful type annotations at important boundaries.
- Validate external inputs explicitly.
- Handle expected failures deliberately.
- Avoid broad exception handling that hides defects.
- Keep constants and configuration understandable.
- Remove unused code, abandoned implementations, and temporary scaffolding.

Comments should explain decisions, constraints, and non-obvious behavior. Do not narrate obvious statements.

Write useful docstrings for public interfaces and complex behavior. Document inputs, outputs, side effects, failure modes, and constraints when relevant. Keep documentation synchronized with implementation.

Use consistent terminology across code, APIs, interface labels, and documentation.

### 14. Reliability, privacy, and testing

Apply access controls consistently to investigations, tools, evidence, stored knowledge, reports, and exports.

Treat retrieved logs, documents, and tool output as data, not instructions that can override permissions.

Keep credentials out of model prompts, reports, and ordinary logs. Scope caches and stored results to the correct environment and access boundaries.

Provide understandable retention controls and a record of important actions.

Test meaningful behavior, especially:

- Investigation state transitions and recovery.
- Tool input validation and permission enforcement.
- Exception matching, expiration, and revocation.
- Evidence provenance.
- Partial failures and unavailable sources.
- Report accuracy.
- Model compatibility failures.
- Cancellation and safe retries.

Include reproducible demonstration scenarios with expected findings and known limitations. Do not rely solely on a model judging its own answers.

### 15. Expected implementation outcome

Deliver a working, documented application with a coherent end-to-end experience:

A user connects an environment and model, asks a question, watches understandable investigative activity, reviews evidence-backed findings, records an intentional exception, and sees that exception correctly applied in a later investigation.

The application should also demonstrate a proposed improvement and subsequent verification.

Provide clear setup, configuration, extension, and contribution documentation. Clearly distinguish implemented functionality from planned functionality.

The finished product should feel consistent across its interface, behavior, and codebase: **powerful investigation capabilities, restrained presentation, explicit evidence, and maintainable Python code.**
