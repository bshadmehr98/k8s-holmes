"""`DemoModel`: a rule-based stand-in for a language model that investigates the demo environment.

It frames the demo questions, calls the capabilities a careful engineer would, and concludes from the
evidence it was shown, citing evidence ids and copying numbers from the results. It is deterministic and
offline, so the demo (design §17.2) and the acceptance tests run without a model endpoint. It is not a
model: it knows only the shapes of the built-in capabilities' results.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from k8sholmes.models import Reply
from k8sholmes.testing.model import calls, structured, text

Kind = str


KIND_WORDS: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    ("verify", ("resolve", "fixed", "did the", "verify", "after the change")),
    ("health", ("restart", "crash", "why is", "failing", "unhealthy")),
    ("unused", ("unused", "obsolete", "nobody", "dead")),
    ("network", ("isolated", "network", "exposed", "external")),
    ("alerts", ("alert", "monitor")),
    ("changes", ("changed", "before", "what change")),
    ("impact", ("affected", "depends", "impact", "if ", "fails")),
    ("safeguards", ("overlook", "practice", "reliab", "replica", "safeguard", "missing")),
)


def _rows(value: Any) -> list[dict[str, Any]]:
    """The records of a list the model was shown; a list cut to size ends with a note, which is skipped,
    and a value cut away entirely is a note, so there are no rows."""
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], v) for v in cast(list[Any], value) if isinstance(v, dict)]


def _classify(question: str) -> Kind:
    q = question.lower()
    for kind, words in KIND_WORDS:
        if any(w in q for w in words):
            return kind
    return "health"


def _name_in(
    question: str,
    candidates: tuple[str, ...] = ("api", "web", "worker", "scheduler", "legacy-export", "postgres"),
) -> str:
    q = question.lower()
    for name in candidates:
        if name in q:
            return name
    return "api"


PLANS: dict[Kind, Callable[[str], list[tuple[str, dict[str, Any]]]]] = {
    "health": lambda n: [
        ("k8s.workload_health", {"kind": "deployment", "name": n, "namespace": "shop"}),
        ("k8s.changes", {"namespace": "shop", "since_hours": 48, "name": n}),
    ],
    "verify": lambda n: [
        ("k8s.workload_health", {"kind": "deployment", "name": n, "namespace": "shop"}),
        ("k8s.changes", {"namespace": "shop", "since_hours": 48, "name": n}),
    ],
    "unused": lambda n: [
        ("k8s.service_backends", {"name": "legacy-export", "namespace": "shop"}),
        ("k8s.safeguards", {"namespaces": ["shop"]}),
    ],
    "network": lambda n: [("k8s.network_exposure", {"namespaces": ["shop"]})],
    "alerts": lambda n: [
        ("k8s.alert_rules", {"namespaces": ["shop"]}),
        ("k8s.safeguards", {"namespaces": ["shop"]}),
    ],
    "changes": lambda n: [("k8s.changes", {"namespace": "shop", "since_hours": 48})],
    "impact": lambda n: [
        ("k8s.inventory", {"namespaces": ["shop"]}),
        ("k8s.service_backends", {"name": n, "namespace": "shop"}),
    ],
    "safeguards": lambda n: [
        ("k8s.safeguards", {"namespaces": ["shop"]}),
        ("k8s.alert_rules", {"namespaces": ["shop"]}),
    ],
}


class DemoModel:
    """A `Model` for the demo: frames, gathers by plan, concludes from the evidence it saw."""

    name = "demo-investigator"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> Reply:
        self.requests.append({"messages": messages, "tools": tools, "schema": schema})
        question = self._question(messages)
        kind = _classify(question)
        name = _name_in(question)
        if schema and schema.get("title") == "framing":
            return self._frame(question, kind, name)
        results = self._tool_results(messages)
        if schema and schema.get("title") == "conclusion":
            return self._conclude(kind, name, results, messages)
        made = {(r["call"]) for r in results}
        available = {t["function"]["name"] for t in tools or []}
        pending = [(cap, args) for cap, args in PLANS[kind](name) if cap not in made and cap in available]
        if not pending or len(results) >= 6:
            return text("I have what I need to conclude.")
        return calls(*pending)

    # reading the thread

    @staticmethod
    def _question(messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if m.get("role") == "user" and not str(m.get("content", "")).startswith(
                ("Investigation context", "Conclude now", "Repair")
            ):
                content = str(m.get("content", ""))
                return content.split("\n", 1)[0].removeprefix("Question: ")
        return ""

    @staticmethod
    def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names: dict[str, str] = {}
        for m in messages:
            for tc in cast(list[dict[str, Any]], m.get("tool_calls") or []):
                names[str(tc.get("id"))] = str(cast(dict[str, Any], tc.get("function", {})).get("name", ""))
        results: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") != "tool":
                continue
            try:
                payload = cast(dict[str, Any], json.loads(str(m.get("content", ""))))
            except json.JSONDecodeError:
                continue
            if "evidence_id" in payload:
                payload["call"] = names.get(str(m.get("tool_call_id")), "")
                results.append(payload)
        return results

    @staticmethod
    def _context(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for m in messages:
            content = str(m.get("content", ""))
            if content.startswith("Investigation context"):
                try:
                    return cast(dict[str, Any], json.loads(content.split("\n", 1)[1]))
                except (json.JSONDecodeError, IndexError):
                    return {}
        return {}

    # phases

    def _frame(self, question: str, kind: Kind, name: str) -> Reply:
        resources = (
            [{"kind": "Deployment", "namespace": "shop", "name": name}]
            if kind in ("health", "verify", "impact")
            else []
        )
        hypotheses = {
            "health": [f"{name} is out of memory", f"a recent change to {name} broke it"],
            "verify": ["the change removed the condition"],
            "unused": [
                "a service without backends or dependents is a candidate; traffic cannot be read here"
            ],
            "network": ["something is exposed outside the cluster", "pods are selected by no network policy"],
            "alerts": ["some workloads are referenced by no alert rule"],
            "changes": ["a revision or scaling event precedes the problem"],
            "impact": [f"workloads that reference {name} would fail with it"],
            "safeguards": ["some workloads lack probes, limits, a PDB or a second replica"],
        }[kind]
        return structured(
            {
                "question": question,
                "namespaces": ["shop"],
                "resources": resources,
                "period_hours": 48,
                "hypotheses": hypotheses,
                "intended_capabilities": [c for c, _ in PLANS[kind](name)],
            }
        )

    def _conclude(
        self, kind: Kind, name: str, results: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> Reply:
        by_call = {r["call"]: r for r in results}
        findings: list[dict[str, Any]] = []
        limitations: list[str] = []
        next_steps: list[str] = []
        answer = "The evidence gathered did not settle the question."
        if "k8s.workload_health" in by_call:
            answer, found, limits, steps = self._from_health(
                name, by_call["k8s.workload_health"], by_call.get("k8s.changes"), verify=kind == "verify"
            )
            findings += found
            limitations += limits
            next_steps += steps
        if "k8s.service_backends" in by_call and kind == "unused":
            answer, found, limits = self._from_backends(by_call["k8s.service_backends"])
            findings += found
            limitations += limits
        if kind == "impact" and "k8s.inventory" in by_call and "k8s.service_backends" in by_call:
            answer, found = self._from_impact(name, by_call["k8s.inventory"], by_call["k8s.service_backends"])
            findings += found
        if "k8s.network_exposure" in by_call:
            answer, found, limits = self._from_exposure(by_call["k8s.network_exposure"])
            findings += found
            limitations += limits
        if "k8s.alert_rules" in by_call and kind == "alerts":
            answer, found, limits = self._from_alerts(by_call["k8s.alert_rules"])
            findings += found
            limitations += limits
        if "k8s.safeguards" in by_call and kind in ("alerts", "safeguards"):
            found = self._from_safeguards(by_call["k8s.safeguards"])
            findings += found
            if kind == "safeguards":
                answer = f"{len(found)} safeguards are missing across the workloads of shop."
        if "k8s.changes" in by_call and kind == "changes":
            answer, found = self._from_changes(by_call["k8s.changes"])
            findings += found
        context = self._context(messages)
        knowledge_used = [
            str(e["id"])
            for e in cast(list[dict[str, Any]], context.get("accepted_exceptions (not evidence)") or [])
            if any(f["condition"] == e.get("condition") for f in findings)
        ]
        knowledge_used += [
            str(n["id"])
            for n in cast(list[dict[str, Any]], context.get("user_provided_knowledge (not evidence)") or [])
        ][:3]
        return structured(
            {
                "answer": answer,
                "findings": findings,
                "limitations": limitations,
                "next_steps": next_steps,
                "knowledge_used": knowledge_used,
            }
        )

    @staticmethod
    def _finding(
        title: str,
        explanation: str,
        condition: str,
        resources: list[dict[str, Any]],
        evidence: list[str],
        *,
        kind: str = "observation",
        uncertainty: str = "",
        missing: list[str] | None = None,
        steps: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "title": title,
            "explanation": explanation,
            "kind": kind,
            "condition": condition,
            "resources": resources,
            "evidence_for": evidence,
            "evidence_against": [],
            "uncertainty": uncertainty,
            "missing_information": missing or [],
            "next_steps": steps or [],
        }

    def _from_health(
        self, name: str, health: dict[str, Any], changes: dict[str, Any] | None, *, verify: bool
    ) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
        data = cast(dict[str, Any], health.get("data") or {})
        if isinstance(data, str):
            return (
                "The workload's health result was too large to read in full.",
                [],
                ["the health result was truncated for the model"],
                [],
            )
        pods = _rows(data.get("pods"))
        ref = [{"kind": "Deployment", "namespace": "shop", "name": name}]
        oom = [
            (p, s)
            for p in pods
            for s in cast(
                list[dict[str, Any]], cast(dict[str, Any], p["summary"]).get("container_states") or []
            )
            if s.get("last_terminated") == "OOMKilled"
        ]
        restarts = sum(int(cast(dict[str, Any], p["summary"]).get("restarts") or 0) for p in pods)
        containers = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], cast(dict[str, Any], data.get("workload") or {}).get("summary") or {}).get(
                "containers"
            )
            or [],
        )
        limit = (
            str(cast(dict[str, Any], containers[0].get("limits") or {}).get("memory", "unknown"))
            if containers
            else "unknown"
        )
        findings: list[dict[str, Any]] = []
        limitations: list[str] = []
        steps: list[str] = []
        if oom:
            previous = cast(dict[str, Any], data.get("previous_logs") or {})
            log_note = (
                " The previous container's log ends with an out-of-memory message."
                if any("out of memory" in str(v) for v in previous.values())
                else ""
            )
            findings.append(
                self._finding(
                    f"{name} is OOMKilled under its {limit} memory limit",
                    f"{len(oom)} of {len(pods)} pods of {name} were last terminated with OOMKilled; the pods "
                    f"restarted {restarts} times. The container's memory limit is {limit}.{log_note}",
                    "pod.crashloop.oom",
                    ref,
                    [str(health["evidence_id"])],
                    uncertainty="whether memory use grows with load or is fixed by configuration",
                    missing=["memory use over time from a metrics source"],
                    steps=[
                        f"raise the memory limit of {name} or lower its cache size",
                        "watch restarts after the change",
                    ],
                )
            )
            steps.append(f"raise the memory limit of {name}")
            if changes and isinstance(changes.get("data"), dict):
                revisions = [
                    c
                    for c in cast(
                        list[dict[str, Any]], cast(dict[str, Any], changes["data"]).get("changes") or []
                    )
                    if c.get("kind") == "revision" and c.get("change_cause")
                ]
                if revisions:
                    rev = revisions[-1]
                    findings.append(
                        self._finding(
                            f"a revision of {name} lowered its memory limit before the restarts",
                            f"Revision {rev.get('revision')} of {name} was created at {rev.get('time')} "
                            f"with the change cause {str(rev.get('change_cause'))!r}; the restarts follow.",
                            "change.before-incident",
                            ref,
                            [str(changes["evidence_id"])],
                            kind="hypothesis",
                            uncertainty=(
                                "the revision precedes the restarts; that it caused them is inferred from "
                                "the limit and the OOMKilled reason"
                            ),
                        )
                    )
            answer = (
                f"{name} restarts because its container is killed for exceeding its {limit} memory limit; "
                "a recent revision lowered that limit."
            )
        elif verify:
            findings.append(
                self._finding(
                    f"{name} is healthy after the change",
                    f"All {len(pods)} pods of {name} are ready with {restarts} restarts; none terminated.",
                    "custom.healthy-after-change",
                    ref,
                    [str(health["evidence_id"])],
                    steps=["keep watching restarts for a day"],
                )
            )
            answer = (
                f"The change appears to have resolved it: {name}'s pods are ready with {restarts} restarts; "
                "recovery is verified only by a verification run."
            )
        else:
            answer = f"{name} shows no crash: {len(pods)} pods, {restarts} restarts."
        return answer, findings, limitations, steps

    def _from_backends(self, backends: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
        data = cast(dict[str, Any], backends.get("data") or {})
        service = cast(dict[str, Any], cast(dict[str, Any], data.get("service") or {}).get("ref") or {})
        name = str(service.get("name", "service"))
        ref = [{"kind": "Service", "namespace": "shop", "name": name}]
        traffic = (
            "traffic to services is cannot_answer with Kubernetes as the only source; a metrics or proxy "
            "source would settle it"
        )
        if backends.get("coverage") != "observed_absent":
            return f"{name} has backends; nothing in the cluster marks it unused.", [], [traffic]
        finding = self._finding(
            f"{name} has no backends",
            f"The selector of {name} matches no pod; it has {data.get('ready_endpoints', 0)} ready "
            "endpoints, so requests to it fail. Whether anyone still calls it cannot be read here.",
            "service.no-backends",
            ref,
            [str(backends["evidence_id"])],
            uncertainty="traffic to the service is not observable here",
            missing=["request counts from a metrics or proxy source"],
            steps=["check the ingress and client configuration for callers before removing it"],
        )
        answer = (
            f"{name} has no backends and may be obsolete, but traffic is not observable here, "
            "so it cannot be called unused."
        )
        return answer, [finding], [traffic]

    def _from_impact(
        self, name: str, inventory: dict[str, Any], backends: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Dependents of the service from the inventory's relationships, listed by basis (design §4.3)."""
        raw = inventory.get("data")
        data = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        by_basis: dict[str, list[str]] = {"defined": [], "observed": [], "inferred": []}
        relationships = data.get("relationships")
        for r in _rows(relationships):
            target = cast(dict[str, Any], r.get("target") or {})
            if target.get("kind") == "Service" and target.get("name") == name and r.get("kind") != "owns":
                source = cast(dict[str, Any], r.get("source") or {})
                by_basis[str(r.get("basis"))].append(f"{source.get('kind')}/{source.get('name')}")
        pods = cast(list[Any], cast(dict[str, Any], backends.get("data") or {}).get("matching_pods") or [])
        parts = [f"{basis}: {', '.join(sorted(set(names))) or 'none'}" for basis, names in by_basis.items()]
        explanation = (
            f"If {name} fails, its {len(pods)} backing pods stop answering. Dependents by basis, "
            + "; ".join(parts)
            + ". Inferred dependents come from names in arguments or environment values and are listed apart."
        )
        ref = [{"kind": "Service", "namespace": "shop", "name": name}]
        evidence = [str(inventory["evidence_id"]), str(backends["evidence_id"])]
        finding = self._finding(
            f"what depends on {name}",
            explanation,
            "custom.impact",
            ref,
            evidence,
            kind="hypothesis",
            uncertainty="inferred dependents are not confirmed by configuration or endpoints",
        )
        return f"If {name} fails: {'; '.join(parts)}.", [finding]

    def _from_exposure(self, exposure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
        data = cast(dict[str, Any], exposure.get("data") or {})
        ev = [str(exposure["evidence_id"])]
        findings: list[dict[str, Any]] = []
        for svc in _rows(data.get("exposed_services")):
            name = str(svc["service"]).split("/")[-1]
            findings.append(
                self._finding(
                    f"{name} is exposed by a {svc.get('type')} service",
                    f"Service {name} is of type {svc.get('type')} and reaches outside the cluster.",
                    "exposure.loadbalancer",
                    [{"kind": "Service", "namespace": "shop", "name": name}],
                    ev,
                    steps=["confirm the exposure is intended and fronted by the ingress"],
                )
            )
        unselected = cast(list[str], data.get("pods_selected_by_no_policy") or [])
        if unselected:
            findings.append(
                self._finding(
                    f"{len(unselected)} pods are selected by no network policy",
                    f"{len(unselected)} pods in shop are selected by no NetworkPolicy, so traffic "
                    "between them is unrestricted inside the cluster.",
                    "policy.unselected-pods",
                    [{"kind": "Namespace", "name": "shop"}],
                    ev,
                    steps=["add a default-deny policy and allow the needed flows"],
                )
            )
        return (
            (
                "Inside the cluster: one LoadBalancer service and most pods selected by no policy; whether "
                "the environment is isolated from external networks cannot be answered from the cluster "
                "alone."
            ),
            findings,
            ["anything outside the cluster (cloud firewalls, a mesh, DNS) is cannot_answer"],
        )

    def _from_alerts(self, alerts: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
        if alerts.get("coverage") == "cannot_answer":
            return (
                (
                    "No alert rule source exists in this cluster, so which alerts are missing cannot be "
                    "answered."
                ),
                [],
                ["the PrometheusRule CRD is absent: alert coverage is cannot_answer"],
            )
        data = cast(dict[str, Any], alerts.get("data") or {})
        without = cast(list[str], data.get("workloads_without_alerts") or [])
        findings = [
            self._finding(
                f"{w.split('/')[-1]} is referenced by no alert rule",
                f"No PrometheusRule alert expression or label references {w.split('/')[-1]}; "
                f"{len(cast(list[Any], data.get('rules') or []))} alert rules exist in the scope.",
                "alert.missing",
                [{"kind": w.split("/")[0], "namespace": "shop", "name": w.split("/")[-1]}],
                [str(alerts["evidence_id"])],
                steps=[f"add an availability and a restart alert for {w.split('/')[-1]}"],
            )
            for w in without
        ]
        return (
            f"{len(without)} workloads are referenced by no alert rule; whether one fired cannot be read.",
            findings,
            ["whether alerts fired needs the alert manager: cannot_answer"],
        )

    def _from_safeguards(self, safeguards: dict[str, Any]) -> list[dict[str, Any]]:
        data = cast(dict[str, Any], safeguards.get("data") or {})
        ev = [str(safeguards["evidence_id"])]
        findings: list[dict[str, Any]] = []
        for row in _rows(data.get("workloads")):
            kind, name = str(row["workload"]).split("/")[0], str(row["workload"]).split("/")[-1]
            ref = [{"kind": kind, "namespace": "shop", "name": name}]
            if kind != "DaemonSet" and (row.get("replicas") or 0) <= 1:
                findings.append(
                    self._finding(
                        f"{name} runs {row.get('replicas')} replica",
                        f"{name} has {row.get('replicas')} replica, so any disruption takes it down.",
                        "replicas.single",
                        ref,
                        ev,
                        steps=[f"run 2 replicas of {name}, or accept it as intentional"],
                    )
                )
            if not row.get("liveness_probe"):
                findings.append(
                    self._finding(
                        f"{name} has no liveness probe",
                        f"No container of {name} declares a liveness probe.",
                        "probe.missing",
                        ref,
                        ev,
                    )
                )
            if not row.get("limits"):
                findings.append(
                    self._finding(
                        f"{name} has no resource limits",
                        f"No container of {name} declares resource limits.",
                        "limits.missing",
                        ref,
                        ev,
                    )
                )
        return findings

    def _from_changes(self, changes: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        data = cast(dict[str, Any], changes.get("data") or {})
        items = _rows(data.get("changes"))
        revisions = [c for c in items if c.get("kind") == "revision"]
        findings = [
            self._finding(
                f"revision {c.get('revision')} of {str(c.get('resource')).split('/')[-1]}",
                f"{c.get('resource')} was created at {c.get('time')}"
                + (f" with the change cause {str(c.get('change_cause'))!r}" if c.get("change_cause") else ""),
                "change.before-incident",
                [{"kind": "ReplicaSet", "namespace": "shop", "name": str(c.get("resource")).split("/")[-1]}],
                [str(changes["evidence_id"])],
            )
            for c in revisions
        ]
        return f"{len(items)} changes in the period, {len(revisions)} of them revisions.", findings
