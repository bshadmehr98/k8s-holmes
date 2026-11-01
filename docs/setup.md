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

## Users and roles

`viewer` reads; `investigator` runs inspect capabilities, edits findings, writes knowledge and accepts
exceptions; `operator` also runs probes and, later, proposals and verifications; an administrator does
everything, including Settings. Roles are granted per environment.

## The demo

`k8sholmes demo` creates the environment `demo` from a recorded cluster, a rule-based offline investigator
and the user `demo` (password `demo-password`, an operator on the demo only). See [the demo](demo.md).
