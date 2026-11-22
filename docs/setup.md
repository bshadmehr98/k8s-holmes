# Setup

## Install

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). From a clone: `uv sync`, then `uv run k8sholmes …`.
Real environments need `kubectl` on the PATH; the default test suite and the demo do not.

## The home folder

`$K8SHOLMES_HOME`, or `~/.k8sholmes`: `k8sholmes.db` (the store), `secrets.json` (model keys, mode 0600),
`setup-token` (until the first administrator exists), `recordings/` (the demo), `capabilities/*.toml`
(command capabilities, see [extensions](extensions.md)) and `allowed-executables.txt` (their allow-list).

## The first administrator

`k8sholmes serve` prints a one-time link on its first start (`k8sholmes setup` prints it again). Open it,
choose a username and a password of at least 8 characters. Everything else happens in Settings.

## Environments

An environment is a kubeconfig context on the host that runs K8sHolmes. Only the context's name is stored;
`kubectl` reads the kubeconfig itself. **Connect** reads the server version and the permission matrix, which
decides which capabilities are available. Settings per environment: probes on or off, the authorization
path for change capabilities (none ships in 0.1), the default model endpoint, retention days.

## Models

Any OpenAI-compatible chat completions endpoint. The key goes to `secrets.json` or comes from an
environment variable you name. **Run capability tests** before use: connectivity, authentication, the named
model, tool calling, streaming, structured replies, limits. An endpoint whose tool-calling test failed
cannot be chosen for investigations. Passing tests do not prove the model investigates well.

**Tool mode.** `native` offers the capabilities as tools and the model writes tool calls. `structured` asks
for two small JSON answers per step, which capability next and then its inputs, under the server's
schema, so a small local model (Ollama, vLLM, llama.cpp) cannot send malformed calls and chooses from a
short list. `auto`, the default, is `structured` once the structured-replies test has passed. Set it,
with an optional **temperature** (0 to 0.3 suits local models) and a **context** limit in tokens, under
the endpoint in Settings → Models. Ollama cuts a prompt longer than its context length without saying
so (set by `OLLAMA_CONTEXT_LENGTH`): keep the endpoint's context at or
below it, and older results are then shown to the model by their summary and evidence id.

## Users and roles

`viewer` reads; `investigator` runs inspect capabilities, edits findings, writes knowledge, accepts
exceptions, generates and edits reports, proposes improvements and saves investigations; `operator` also
runs probes, approves proposals, marks them applied and verifies them; an administrator does everything,
including Settings and the view-only share links of reports. Roles are granted per environment.

## Schedules and notifications

Saved investigations run on a schedule only while `k8sholmes serve` runs: its scheduler thread checks every
minute. A saved investigation can notify a webhook; the URL is read from an environment variable of the
server process that you name in the saved investigation (for example `K8SHOLMES_HOOK_SHOP`), is never
stored, and must be `http(s)`. The payload is the run's summary and a link, never evidence. The same thread
runs the retention purge once a day (Settings → Retention sets the days per environment).

## The demo

`k8sholmes demo` creates the environment `demo` from a recorded cluster, a rule-based offline investigator
and the user `demo` (password `demo-password`, an operator on the demo only). See [the demo](demo.md).
