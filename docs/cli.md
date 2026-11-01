# The command line

Every command takes `--home DIR` (default `$K8SHOLMES_HOME` or `~/.k8sholmes`). Exit codes: 0 success, 1 a
failure with a message on stderr, 2 usage.

```text
k8sholmes demo [--after-fix] [--question "…"]
```
Sets up the demo environment (a recorded cluster), its offline investigator and the user `demo`; with
`--question` runs one investigation and prints its report. `--after-fix` records the state after the
api's memory limit was raised.

```text
k8sholmes serve [--host 127.0.0.1] [--port 8800] [--allow-remote]
```
The web interface. It binds the loopback interface; another host needs `--allow-remote` and TLS in front
(design §15.5). On a first start it prints the one-time link that creates the first administrator.

```text
k8sholmes setup
```
Prints that link again (the token also lives in the home folder as `setup-token`). Exit 1 once an
administrator exists.

```text
k8sholmes investigate "QUESTION" --env NAME [--namespace NS]… [--model NAME] [--user NAME] [--json]
```
Runs an investigation and prints its report (or the whole record as JSON). Progress goes to stderr. Exit 1
when the investigation ends `failed` or the environment, user or model is unknown.

```text
k8sholmes capabilities [--env NAME] [--json]
k8sholmes environments
k8sholmes export INVESTIGATION_ID --out DIR
```
The catalog (with availability in an environment), the environments and when they were connected, and an
investigation written as `report.md`, `report.json` (`format_version "1"`) and one JSON file per piece of
evidence.
