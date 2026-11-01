"""The built-in Kubernetes inspect capabilities that read one thing (design §5.3).

Every capability here is a plain function over the `Cluster` port plus a pydantic input model. Each
result states its coverage: what was read is `observed`; an empty answer is `observed_absent` when the
source could have shown it and `no_data` when it could not (events expire, a log is empty).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from k8sholmes.capabilities import Capability, Context
from k8sholmes.cluster import canonical_kind
from k8sholmes.environments import take_inventory
from k8sholmes.errors import NotFound
from k8sholmes.evidence import Result
from k8sholmes.inventory import WORKLOAD_KINDS, get, item_of, parse_time
from k8sholmes.records import ResourceRef, now

K8S = ("kubernetes",)


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")


# k8s.inventory


class InventoryInputs(Inputs):
    namespaces: list[str] = Field(
        default=[], description="Namespaces to list; empty means every namespace the permissions allow."
    )


def run_inventory(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(InventoryInputs, inputs)
    inventory = take_inventory(
        ctx.store, ctx.environment, ctx.cluster, i.namespaces, max_items=ctx.settings.max_items
    )
    counts: dict[str, int] = {}
    for item in inventory.items:
        counts[item.ref.kind] = counts.get(item.ref.kind, 0) + 1
    data: dict[str, Any] = {
        "inventory_id": inventory.id,
        "taken_at": inventory.taken_at.isoformat(),
        "namespaces": inventory.namespaces,
        "counts": counts,
        "items": [
            {
                "ref": item.ref.model_dump(),
                "labels": item.labels,
                "owner": item.owner.model_dump() if item.owner else None,
                "summary": item.summary,
            }
            for item in inventory.items
        ],
        "relationships": [r.model_dump() for r in inventory.relationships],
        "unreadable": inventory.unreadable,
        "truncated": inventory.truncated,
    }
    reason = (
        "every kind listed"
        if not inventory.unreadable
        else "unreadable: " + ", ".join(f"{k} ({v})" for k, v in inventory.unreadable.items())
    )
    summary = (
        f"{len(inventory.items)} resources, {len(counts)} kinds, {len(inventory.relationships)} relationships"
    )
    return Result(summary=summary, data=data, coverage="observed", coverage_reason=reason)


# k8s.get and k8s.list


class GetInputs(Inputs):
    kind: str = Field(
        description="Kind, singular or plural, as kubectl accepts it (pod, deployment, service, …)."
    )
    name: str
    namespace: str | None = Field(
        default=None, description="Omit for cluster-scoped kinds (node, namespace)."
    )


def run_get(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(GetInputs, inputs)
    try:
        resource = ctx.cluster.get(i.kind, i.name, namespace=i.namespace)
    except NotFound as exc:
        return Result(
            summary=str(exc),
            data=None,
            coverage="observed_absent",
            coverage_reason="the cluster has no such resource",
        )
    item = item_of(resource)
    data = {
        "resource": resource,
        "summary": item.summary,
        "labels": item.labels,
        "owner": item.owner.model_dump() if item.owner else None,
    }
    return Result(
        summary=f"{item.ref.kind} {item.ref.namespace or ''}/{item.ref.name}", data=data, coverage="observed"
    )


class ListInputs(Inputs):
    kind: str
    namespace: str | None = Field(default=None, description="Omit for every namespace.")
    selector: str | None = Field(default=None, description="A label selector such as app=api,tier!=cache.")
    field_selector: str | None = Field(
        default=None, description="A field selector such as status.phase=Pending."
    )


def run_list(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ListInputs, inputs)
    resources = ctx.cluster.list(
        i.kind, namespace=i.namespace, selector=i.selector, field_selector=i.field_selector
    )
    truncated = len(resources) > ctx.settings.max_items
    resources = resources[: ctx.settings.max_items]
    items = [
        {"ref": item_of(r).ref.model_dump(), "labels": item_of(r).labels, "summary": item_of(r).summary}
        for r in resources
    ]
    kind = canonical_kind(i.kind)
    if not items:
        return Result(
            summary=f"no {kind} matches",
            data={"items": []},
            coverage="observed_absent",
            coverage_reason="the list came back empty",
        )
    note = " (truncated)" if truncated else ""
    return Result(
        summary=f"{len(items)} {kind}{note}",
        data={"items": items, "truncated": truncated},
        coverage="observed",
    )


# k8s.events


class EventsInputs(Inputs):
    namespace: str | None = None
    kind: str | None = Field(default=None, description="With name: only events about this resource.")
    name: str | None = None
    since_hours: float = Field(default=24, ge=0, le=24 * 30, description="How far back to look.")


def _event_row(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": e.get("lastTimestamp") or e.get("eventTime") or get(e, "metadata", "creationTimestamp"),
        "first": e.get("firstTimestamp"),
        "type": e.get("type"),
        "reason": e.get("reason"),
        "object": f"{get(e, 'involvedObject', 'kind')}/{get(e, 'involvedObject', 'name')}",
        "namespace": get(e, "involvedObject", "namespace") or get(e, "metadata", "namespace"),
        "message": e.get("message"),
        "count": e.get("count") or get(e, "series", "count") or 1,
    }


def run_events(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(EventsInputs, inputs)
    involved = ResourceRef(kind=i.kind, namespace=i.namespace, name=i.name) if i.kind and i.name else None
    events = ctx.cluster.events(namespace=i.namespace, involved=involved)
    start = (ctx.now or now()) - timedelta(hours=i.since_hours)
    rows = [_event_row(e) for e in events]
    recent = [r for r in rows if (t := parse_time(r["time"])) is not None and t >= start]
    recent = recent[-ctx.settings.max_items :]
    if not recent:
        coverage = "observed_absent" if i.since_hours <= 1 else "no_data"
        reason = (
            "no events in the period; the cluster keeps events for about an hour by default, so an empty "
            "answer for a longer period does not mean nothing happened"
        )
        return Result(
            summary="no events in the period",
            data={"events": [], "period_start": start.isoformat()},
            coverage=coverage,
            coverage_reason=reason,
        )
    warnings = sum(1 for r in recent if r["type"] == "Warning")
    return Result(
        summary=f"{len(recent)} events, {warnings} warnings, since {start.isoformat(timespec='minutes')}",
        data={"events": recent, "period_start": start.isoformat()},
        coverage="observed",
    )


# k8s.logs


class LogsInputs(Inputs):
    pod: str
    namespace: str
    container: str | None = None
    previous: bool = Field(
        default=False, description="The previous (crashed) container's log instead of the current one."
    )
    tail: int = Field(default=200, ge=1, le=5000)
    since_hours: float | None = Field(default=None, ge=0)


def run_logs(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(LogsInputs, inputs)
    since = (ctx.now or now()) - timedelta(hours=i.since_hours) if i.since_hours else None
    text = ctx.cluster.logs(
        i.pod, namespace=i.namespace, container=i.container, previous=i.previous, tail=i.tail, since=since
    )
    which = "previous" if i.previous else "current"
    if not text.strip():
        return Result(
            summary=f"no {which} log lines for {i.pod}",
            data="",
            coverage="no_data",
            coverage_reason="the log is empty or rotated away",
        )
    lines = text.count("\n")
    return Result(
        summary=f"{lines} {which} log lines of {i.pod}" + (f"/{i.container}" if i.container else ""),
        data=text,
        coverage="observed",
    )


# k8s.workload_health


class WorkloadInputs(Inputs):
    kind: str = Field(description="Deployment, StatefulSet, DaemonSet, Job, CronJob or Pod.")
    name: str
    namespace: str


def _unhealthy(pod_summary: dict[str, Any]) -> bool:
    return (
        pod_summary.get("phase") not in ("Running", "Succeeded")
        or not pod_summary.get("ready")
        or int(pod_summary.get("restarts") or 0) > 0
        or any(
            s.get("waiting") for s in cast(list[dict[str, Any]], pod_summary.get("container_states") or [])
        )
    )


def run_workload_health(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(WorkloadInputs, inputs)
    kind = canonical_kind(i.kind)
    try:
        resource = ctx.cluster.get(kind, i.name, namespace=i.namespace)
    except NotFound as exc:
        return Result(
            summary=str(exc), data=None, coverage="observed_absent", coverage_reason="no such workload"
        )
    workload = item_of(resource)
    selector = cast(dict[str, str], workload.summary.get("selector") or {})
    if kind == "Pod":
        pods = [resource]
    elif selector:
        pods = ctx.cluster.list(
            "pods", namespace=i.namespace, selector=",".join(f"{k}={v}" for k, v in selector.items())
        )
    else:
        pods = []
    pod_items = [item_of(p) for p in pods]
    owners: list[dict[str, Any]] = []
    if kind == "Deployment":
        owners = (
            [
                item_of(rs).model_dump(mode="json")
                for rs in ctx.cluster.list(
                    "replicasets",
                    namespace=i.namespace,
                    selector=",".join(f"{k}={v}" for k, v in selector.items()),
                )
            ]
            if selector
            else []
        )
    events = [
        _event_row(e)
        for e in ctx.cluster.events(
            namespace=i.namespace, involved=ResourceRef(kind=kind, namespace=i.namespace, name=i.name)
        )
    ]
    unhealthy = [p for p in pod_items if _unhealthy(p.summary)][:3]
    pod_events: dict[str, list[dict[str, Any]]] = {}
    previous_logs: dict[str, str] = {}
    for p in unhealthy:
        pod_events[p.ref.name] = [
            _event_row(e) for e in ctx.cluster.events(namespace=i.namespace, involved=p.ref)
        ][-20:]
        for state in cast(list[dict[str, Any]], p.summary.get("container_states") or []):
            if state.get("last_terminated") or int(state.get("restarts") or 0) > 0:
                try:
                    text = ctx.cluster.logs(
                        p.ref.name,
                        namespace=i.namespace,
                        container=str(state.get("name")),
                        previous=True,
                        tail=50,
                    )
                except NotFound:
                    text = ""
                if text.strip():
                    previous_logs[f"{p.ref.name}/{state.get('name')}"] = text
    data = {
        "workload": {
            "ref": workload.ref.model_dump(),
            "summary": workload.summary,
            "labels": workload.labels,
        },
        "pods": [
            {
                "ref": p.ref.model_dump(),
                "summary": p.summary,
                "owner": p.owner.model_dump() if p.owner else None,
            }
            for p in pod_items
        ],
        "owner_chain": owners,
        "events": events[-50:],
        "unhealthy_pod_events": pod_events,
        "previous_logs": previous_logs,
    }
    ready = sum(1 for p in pod_items if p.summary.get("ready"))
    restarts = sum(int(p.summary.get("restarts") or 0) for p in pod_items)
    counts = f"{ready}/{len(pod_items)} pods ready, {len(unhealthy)} unhealthy, {restarts} restarts"
    summary = f"{kind} {i.name}: {counts}"
    return Result(summary=summary, data=data, coverage="observed")


# k8s.changes


class ChangesInputs(Inputs):
    namespace: str
    since_hours: float = Field(default=24, ge=0, le=24 * 90)
    name: str | None = Field(default=None, description="Only changes about resources with this name.")


_CHANGE_REASONS = {
    "ScalingReplicaSet",
    "SuccessfulCreate",
    "SuccessfulDelete",
    "Created",
    "Killing",
    "Scheduled",
    "Pulled",
    "NodeNotReady",
    "Evicted",
    "Preempted",
}


def run_changes(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ChangesInputs, inputs)
    start = (ctx.now or now()) - timedelta(hours=i.since_hours)
    changes: list[dict[str, Any]] = []
    for rs in ctx.cluster.list("replicasets", namespace=i.namespace):
        item = item_of(rs)
        created = item.created_at
        if (
            created
            and created >= start
            and (not i.name or (item.owner and item.owner.name == i.name) or item.ref.name == i.name)
        ):
            changes.append(
                {
                    "time": created.isoformat(),
                    "kind": "revision",
                    "resource": f"ReplicaSet/{item.ref.name}",
                    "owner": item.owner.model_dump() if item.owner else None,
                    "revision": item.summary.get("revision"),
                    "change_cause": item.summary.get("change_cause"),
                    "images": [
                        c.get("image")
                        for c in cast(list[dict[str, Any]], item.summary.get("containers") or [])
                    ],
                    "replicas": item.summary.get("replicas"),
                }
            )
    for kind in (
        "deployments",
        "statefulsets",
        "daemonsets",
        "services",
        "configmaps",
        "ingresses",
        "networkpolicies",
        "cronjobs",
        "jobs",
    ):
        for resource in ctx.cluster.list(kind, namespace=i.namespace):
            item = item_of(resource)
            if i.name and item.ref.name != i.name:
                continue
            if item.created_at and item.created_at >= start:
                changes.append(
                    {
                        "time": item.created_at.isoformat(),
                        "kind": "created",
                        "resource": f"{item.ref.kind}/{item.ref.name}",
                    }
                )
            gen, seen = item.summary.get("generation"), item.summary.get("observed_generation")
            if gen is not None and seen is not None and gen != seen:
                changes.append(
                    {
                        "time": None,
                        "kind": "rollout_in_progress",
                        "resource": f"{item.ref.kind}/{item.ref.name}",
                        "generation": gen,
                        "observed_generation": seen,
                    }
                )
    events = [_event_row(e) for e in ctx.cluster.events(namespace=i.namespace)]
    event_changes = [
        {
            "time": r["time"],
            "kind": "event",
            "resource": r["object"],
            "reason": r["reason"],
            "message": r["message"],
        }
        for r in events
        if r["reason"] in _CHANGE_REASONS
        and (t := parse_time(r["time"])) is not None
        and t >= start
        and (not i.name or i.name in str(r["object"]))
    ]
    changes.extend(event_changes)
    changes.sort(key=lambda c: c["time"] or "")
    coverage = "observed"
    reason = f"{len(changes)} changes since {start.isoformat(timespec='minutes')}"
    if not event_changes and i.since_hours > 1:
        coverage = "observed" if changes else "no_data"
        reason += (
            "; the cluster keeps events for about an hour by default, so changes older than that are seen "
            "only through revisions and creation times"
        )
    return Result(
        summary=reason,
        data={"changes": changes, "period_start": start.isoformat()},
        coverage=coverage,
        coverage_reason=reason,
    )


# k8s.permissions and k8s.top


class NoInputs(Inputs):
    pass


def run_permissions(inputs: BaseModel, ctx: Context) -> Result:
    matrix = ctx.environment.permissions
    denied = sorted(k for k, v in matrix.items() if not v)
    return Result(
        summary=f"{len(matrix) - len(denied)} of {len(matrix)} permissions allowed"
        + (f"; denied: {', '.join(denied)}" if denied else ""),
        data={
            "permissions": matrix,
            "denied": denied,
            "checked_at": ctx.environment.connected_at.isoformat() if ctx.environment.connected_at else None,
        },
        coverage="observed",
    )


class TopInputs(Inputs):
    kind: str = Field(default="pods", description="pods or nodes")
    namespace: str | None = None


def run_top(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(TopInputs, inputs)
    rows = ctx.cluster.top(i.kind, namespace=i.namespace)
    if rows is None:
        return Result(
            summary="no metrics server",
            data=None,
            coverage="cannot_answer",
            coverage_reason="the cluster has no metrics API; CPU and memory use cannot be read",
        )
    return Result(
        summary=f"{len(rows)} {i.kind} with current CPU and memory", data={"rows": rows}, coverage="observed"
    )


def _cap(
    id: str,
    title: str,
    purpose: str,
    inputs: type[BaseModel],
    run: Any,
    permissions: tuple[tuple[str, str], ...],
    coverage: tuple[str, ...],
) -> Capability:
    return Capability(
        id=id,
        title=title,
        purpose=purpose,
        class_="inspect",
        systems=K8S,
        inputs=inputs,
        permissions=permissions,
        run=run,
        coverage=coverage,
    )


LIST_ALL: tuple[tuple[str, str], ...] = tuple(
    ("list", k)
    for k in (
        "pods",
        "deployments",
        "replicasets",
        "statefulsets",
        "daemonsets",
        "jobs",
        "cronjobs",
        "services",
        "endpointslices",
        "ingresses",
        "networkpolicies",
        "persistentvolumeclaims",
        "horizontalpodautoscalers",
        "poddisruptionbudgets",
        "serviceaccounts",
        "configmaps",
        "secrets",
    )
)

CAPABILITIES: list[Capability] = [
    _cap(
        "k8s.inventory",
        "Inventory of a scope",
        (
            "What exists in the scope and how it relates: workloads, pods, services, ingresses, policies, "
            "volumes, autoscalers, budgets, config names; relationships marked defined, observed or "
            "inferred. Start here for questions about a whole namespace."
        ),
        InventoryInputs,
        run_inventory,
        LIST_ALL,
        ("observed", "no_data"),
    ),
    _cap(
        "k8s.get",
        "One resource",
        (
            "One resource as the cluster returns it, with a summary of the fields that matter. Secrets: "
            "metadata only, never the body."
        ),
        GetInputs,
        run_get,
        (("get", "pods"),),
        ("observed", "observed_absent"),
    ),
    _cap(
        "k8s.list",
        "Resources of a kind",
        (
            "Resources of one kind in a scope, with a label or field selector; summaries, not full bodies. "
            "Capped."
        ),
        ListInputs,
        run_list,
        (("list", "pods"),),
        ("observed", "observed_absent"),
    ),
    _cap(
        "k8s.events",
        "Events",
        (
            "Events in a namespace or about one resource, in a period. Events expire after about an hour, "
            "so an empty answer for a longer period is no_data, not absence."
        ),
        EventsInputs,
        run_events,
        (("list", "events"),),
        ("observed", "observed_absent", "no_data"),
    ),
    _cap(
        "k8s.logs",
        "Container logs",
        "The tail of a container's log, current or previous (crashed) container, optionally since a time.",
        LogsInputs,
        run_logs,
        (("get", "pods/log"),),
        ("observed", "no_data"),
    ),
    _cap(
        "k8s.workload_health",
        "Health of a workload",
        (
            "A workload's pods with their conditions, restarts, last terminations and waiting reasons; its "
            "owner chain; its events; the events and previous logs of up to three unhealthy pods. Use for "
            "'why is X failing'."
        ),
        WorkloadInputs,
        run_workload_health,
        (("get", "pods"), ("list", "pods"), ("list", "events"), ("get", "pods/log")),
        ("observed", "observed_absent"),
    ),
    _cap(
        "k8s.changes",
        "What changed in a period",
        (
            "Revisions (ReplicaSets with their change cause and images), resources created, rollouts in "
            "progress and change events in a namespace since a time. Use for 'what changed before the "
            "problem'."
        ),
        ChangesInputs,
        run_changes,
        (("list", "replicasets"), ("list", "deployments"), ("list", "events")),
        ("observed", "no_data"),
    ),
    _cap(
        "k8s.permissions",
        "What K8sHolmes may read",
        (
            "The permission matrix of the environment from the last connection check: which verbs on which "
            "kinds the cluster allows."
        ),
        NoInputs,
        run_permissions,
        (),
        ("observed",),
    ),
    _cap(
        "k8s.top",
        "Current CPU and memory",
        "Current CPU and memory of pods or nodes from the metrics server, when one exists.",
        TopInputs,
        run_top,
        (("list", "pods.metrics.k8s.io"),),
        ("observed", "cannot_answer"),
    ),
]

__all__ = ["CAPABILITIES", "WORKLOAD_KINDS"]
