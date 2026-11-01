# The demo

`k8sholmes demo` creates the environment **demo** from a recorded cluster and a rule-based investigator
that runs offline (design §17.2). The cluster is a small shop in the namespace `shop`: `web`, `api`,
`worker`, `scheduler`, `legacy-export` and `postgres`, with conditions planted on purpose. Each scenario
below is tested in `tests/e2e/test_ac22_demo_runs_offline.py`.

```text
k8sholmes demo --question "Why is api restarting?"
```

| Question | Expected findings | Known limitations |
|---|---|---|
| Why is api restarting? | `pod.crashloop.oom` (observation): the api container is OOMKilled under its 256Mi limit; `change.before-incident` (hypothesis): revision 3 lowered the limit | none: everything rests on observed evidence |
| Which services appear unused or obsolete? | `service.no-backends`: `legacy-export`'s selector matches no pod | traffic is `cannot_answer` with Kubernetes as the only source, so nothing is called unused |
| Is this environment isolated from external networks? | `exposure.loadbalancer` on `web`; `policy.unselected-pods` | anything outside the cluster is `cannot_answer` |
| Which important alerts are missing? | `alert.missing` for `api`, `worker`, `scheduler`; `replicas.single` for `scheduler` and `worker` | whether an alert fired is `cannot_answer` |
| What changed before this problem started? | `change.before-incident` for the api revisions | none |
| Did the latest change resolve the api issue? (`--after-fix`) | `custom.healthy-after-change` | recovery is verified only by a verification run (milestone 3) |

Accepting `replicas.single` on `scheduler` as intentional makes the next run show it as `accepted_risk`;
`worker`, a similar finding on another resource, stays open.

The demo plants three secrets (an environment variable, a log line, a container argument) that must appear
in no evidence, model message, report or export; guarantee 15 checks it.
