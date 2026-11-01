# Privacy, secrets and audit

## What is read

Only what the environment's permission matrix allows (`k8sholmes environments`, Settings → Environments):
K8sHolmes asks the cluster `auth can-i` for each verb and kind at connection time and never runs a verb the
matrix denies. The verbs are read-only: `get`, `logs`, `top`, `auth can-i`, `version`, `api-resources`.
The gate checks the verb and kind pairs each capability declares in its catalog entry
(`capabilities.md`), but a capability is not confined to them: `k8s.get` and `k8s.list` read whatever kind
they are asked for, and the inventory-based capabilities read every inventory kind the matrix allows. An
administrator can disable any capability or restrict it to namespaces per environment.

## What is never read

- **Secret bodies.** `k8s.get` and the inventory return a Secret's metadata only; `data` is never fetched.
- **The kubeconfig.** Only the context name is stored; `kubectl` reads the file itself.
- **Exec into pods, writes of any kind.** No capability does either in 0.1.

## What a model receives

The system prompt, the question, the scope and the catalog of capabilities it may use; then each
capability's result as a tool message, after the redactor and the size cap, and the team's knowledge notes
as context (never as evidence). The prompt says that tool results are data: nothing a model says can run a
capability the gate refuses, and an instruction planted in a log line produces a refused call that is
recorded and shown (guarantee 23). The model's key never appears in a message.

## The redactor

Runs on every result before storage and before any model message (design §14.3). It replaces:

- the values of environment variables whose names contain `key`, `token`, `password`, `passwd`,
  `secret`, `credential` or `auth`, and command arguments with such names shaped like `--token VALUE` or
  `--token=VALUE`;
- the values of object keys whose names contain `password`, `passwd`, `token`, `secret`, `credential`,
  `api_key`, `access_key` or `private_key` (with `-` or `_` or nothing between the words), except keys
  that name or point at something (ending in `name`, `ref`, `path` or `file`, such as `secretName`); a bare
  `key` or `auth` key is kept, because manifests use it structurally;
- values shaped like JWTs, AWS keys, GitHub tokens, Google keys, Slack tokens, private keys and basic-auth
  URLs;
- `Authorization: Bearer …` and `Authorization: Basic …` in logs and headers.

Each piece of evidence carries its `redactions` count. The demo plants three secrets (an environment
variable, a log line and a container argument) and guarantee 15 checks that none reaches evidence, a model
message, a report, an export, a notification or the terminal.

## Keys

Model API keys live in `$K8SHOLMES_HOME/secrets.json` (mode 0600) or in an environment variable the
endpoint names; never in the database, a message, a report, an export or a log. Webhook URLs for saved
investigations are read from an environment variable of the server process you name, never stored.

## Retention

Per environment (Settings → Retention): the days to keep short-lived evidence (`k8s.logs`, `k8s.top`) and
the days to keep investigations that nothing references. The purge runs from the server's scheduler once a
day and never removes anything a reviewed finding, an accepted exception, a report revision, a verification
or a saved run references; what it removed is written to the audit log (guarantee 24).

## Audit

The audit log (Settings → Audit) records sign-ins and failed sign-ins, environment and model changes,
capability settings and tests, every refused call, the creation, edit and revocation of exceptions,
finding state changes and edits, proposals and verifications, reports generated and edited, shares created
and revoked, exports, model comparisons and retention purges, each with the user, the environment, the
subject and the time.

## Shares

A share link opens one report revision, read-only, without a session, until it expires (1 to 90 days) or an
administrator revokes it. The token is shown once and stored as a hash; the link carries nothing of the
rest of the environment.
