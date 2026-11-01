# Why k8sholmes exists

> Point it at a pod that keeps restarting, a deployment that will not roll out or a service nobody can
> reach. It gathers the evidence a good engineer would gather, read-only, runs the known failure patterns
> against it, reasons over what it found, and hands back a case file: what is wrong, why, the evidence
> for each claim, and the exact commands that would fix it. It never runs them.

**Status:** this is a proposed design ([changes/0001](changes/0001-initial-design.md)), written on
2026-11-01 from the project's name and the owner's request to set the project up. Nothing is built. The
owner's answers to the open questions in [decisions.md](decisions.md) decide what the product really is.

## The problem

Debugging a Kubernetes failure is a fixed ritual that nobody enjoys: `get`, `describe`, `events`, `logs
--previous`, walk from the pod to its ReplicaSet to its Deployment, check the Service's selector against
the pod's labels, check the PVC, check the node. The ritual has problems:

- **It is done by hand every time**, and the order matters: a Pending pod is a scheduling question, a
  CrashLoopBackOff pod is a log question, and an ImagePullBackOff pod is a registry question. People learn
  the branches one outage at a time.
- **The common causes are known, and still found slowly.** An OOMKilled container, a Service whose
  selector matches no pod, a probe that fails because the port is wrong, a ConfigMap key that does not
  exist, a PVC waiting for a StorageClass that is not there. Each has a signature in the cluster's own
  records. Nothing checks them all at once.
- **The evidence is lost.** The investigation lives in a terminal scrollback and a Slack thread. A week
  later nobody can say what the cluster looked like, what was tried, or why the fix was the fix.
- **AI helpers guess.** A model asked "why is my pod crashing?" with a pasted `describe` is confident and
  often wrong, because it did not see the logs, the events of the owner, or the node. A model that drives
  `kubectl` itself is slow, expensive, hard to audit, and one bad call away from changing the cluster.
- **Secrets leak into the tooling.** `kubectl get secret -o yaml` pasted into a chat, environment values
  in a `describe`, tokens in logs. Diagnostic tools must be built to never see them.

## The core ideas

1. **Code decides first.** The known failure patterns are plain checks in code, each with a stable id,
   the evidence it reads and the finding it produces. They run in milliseconds, offline, deterministically,
   and they are the same every time.
2. **Evidence before conclusions.** Everything the investigation reads from the cluster is kept, as it
   was returned, with a hash and a time. Every finding names the evidence it rests on and the grade of
   that evidence (observed in the cluster, derived by a check, reported by a model). A claim without
   evidence is not a finding.
3. **One model call, over all the evidence, checked by code.** When the checks do not explain the
   symptom, a model is asked once, with the whole redacted case, to connect the evidence into a root
   cause and to say what evidence is missing. Its reply is structured and checked: every reference it
   makes must point at real evidence. Without a model, the case is still complete.
4. **Read-only, provably.** The cluster is reached through one adapter that knows a short list of verbs
   (`get`, `logs`, `events`, `top`, `auth can-i`, `version`, `api-resources`). Secrets are never read:
   their names are known from the references, their values are never fetched. There is no "apply" anywhere
   in the package.
5. **The case file is the product.** An investigation is a folder: the symptom, the evidence, the
   findings, the model's reasoning with what it was sent, the report and the remedies. It opens without a
   cluster, a week later, on another machine.
6. **Remedies are commands you run, not actions it takes.** Every remedy is an exact command or manifest
   change, named for the finding it fixes and marked by its risk. k8sholmes 0.1 changes nothing itself.

## Non-goals (0.1)

Changing the cluster in any way; running inside the cluster as an operator or watching alerts;
security posture or vulnerability scanning; cost analysis; multi-cluster fleets; replacing observability
stacks (it reads the cluster's own records, not Prometheus or Loki; a later change may add them as
evidence sources); a hosted service or telemetry.

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as proposed: concepts, rules and the guarantees the tests will check |
| [`changes/0001-initial-design.md`](changes/0001-initial-design.md) | the first design and its open questions for the owner (proposed) |
| [`history/0000-research.md`](history/0000-research.md) | what exists already (HolmesGPT, k8sgpt), and what should be researched before or after the design is accepted |
| [`decisions.md`](decisions.md) | smaller choices, each awaiting the owner's review |

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [changes/](changes/) and [decisions.md](decisions.md); the
process is in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
