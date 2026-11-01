"""The built-in Kubernetes inspect capabilities that read one thing (design §5.3).

Every capability here is a plain function over the `Cluster` port plus a pydantic input model. Each
result states its coverage: what was read is `observed`; an empty answer is `observed_absent` when the
source could have shown it and `no_data` when it could not (events expire, a log is empty).
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, Field

from k8sholmes.capabilities import Capability, Context, Inputs
from k8sholmes.cluster import canonical_kind
from k8sholmes.environments import take_inventory
from k8sholmes.errors import NotFound
from k8sholmes.evidence import Result
from k8sholmes.inventory import WORKLOAD_KINDS, InventoryItem, dig, item_of, parse_time
from k8sholmes.records import ResourceRef, now

K8S = ("kubernetes",)
INSPECT = functools.partial(Capability, class_="inspect", systems=K8S)
EVENTS_NOTE = (
    "the cluster keeps events for about an hour by default, so an empty answer for a longer period does "
    "not mean nothing happened"
)


def _item_dump(item: InventoryItem) -> dict[str, Any]:
    return {
        "ref": item.ref.model_dump(),
        "labels": item.labels,
        "owner": item.owner.model_dump() if item.owner else None,
        "summary": item.summary,
    }


def _selector_text(selector: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in selector.items())


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
        "items": [_item_dump(item) for item in inventory.items],
        "relationships": [r.model_dump() for r in inventory.relationships],
        "unreadable": inventory.unreadable,
        "truncated": inventory.truncated,
    }
    unreadable = ", ".join(f"{k} ({v})" for k, v in inventory.unreadable.items())
    reason = "every kind listed" if not unreadable else f"unreadable: {unreadable}"
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
    data = {"resource": resource, **_item_dump(item)}
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
    items = [_item_dump(item_of(r)) for r in resources[: ctx.settings.max_items]]
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
        "time": e.get("lastTimestamp") or e.get("eventTime") or dig(e, "metadata", "creationTimestamp"),
        "first": e.get("firstTimestamp"),
        "type": e.get("type"),
        "reason": e.get("reason"),
        "object": f"{dig(e, 'involvedObject', 'kind')}/{dig(e, 'involvedObject', 'name')}",
        "namespace": dig(e, "involvedObject", "namespace") or dig(e, "metadata", "namespace"),
        "message": e.get("message"),
        "count": e.get("count") or dig(e, "series", "count") or 1,
    }


def _since(rows: list[dict[str, Any]], start: datetime) -> list[dict[str, Any]]:
    return [r for r in rows if (t := parse_time(r["time"])) is not None and t >= start]


def run_events(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(EventsInputs, inputs)
    involved = ResourceRef(kind=i.kind, namespace=i.namespace, name=i.name) if i.kind and i.name else None
    start = now() - timedelta(hours=i.since_hours)
    recent = _since(
        [_event_row(e) for e in ctx.cluster.events(namespace=i.namespace, involved=involved)], start
    )
    recent = recent[-ctx.settings.max_items :]
    data = {"events": recent, "period_start": start.isoformat()}
    if not recent:
        coverage = "observed_absent" if i.since_hours <= 1 else "no_data"
        return Result(
            summary="no events in the period",
            data=data,
            coverage=coverage,
            coverage_reason=f"no events in the period; {EVENTS_NOTE}",
        )
    warnings = sum(1 for r in recent if r["type"] == "Warning")
    summary = f"{len(recent)} events, {warnings} warnings, since {start.isoformat(timespec='minutes')}"
    return Result(summary=summary, data=data, coverage="observed")


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
    since = now() - timedelta(hours=i.since_hours) if i.since_hours else None
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
    container = f"/{i.container}" if i.container else ""
    return Result(
        summary=f"{text.count(chr(10))} {which} log lines of {i.pod}{container}",
        data=text,
        coverage="observed",
    )


# k8s.workload_health


class WorkloadInputs(Inputs):
    kind: str = Field(description="Deployment, StatefulSet, DaemonSet, Job, CronJob or Pod.")
    name: str
    namespace: str


def _unhealthy(pod_summary: dict[str, Any]) -> bool:
    states = cast(list[dict[str, Any]], pod_summary.get("container_states") or [])
    return (
        pod_summary.get("phase") not in ("Running", "Succeeded")
        or not pod_summary.get("ready")
        or int(pod_summary.get("restarts") or 0) > 0
        or any(s.get("waiting") for s in states)
    )


def _pods_of(
    ctx: Context, kind: str, workload: InventoryItem, resource: dict[str, Any]
) -> list[InventoryItem]:
    if kind == "Pod":
        return [item_of(resource)]
    selector = cast(dict[str, str], workload.summary.get("selector") or {})
    if not selector:
        return []
    return [
        item_of(p)
        for p in ctx.cluster.list("pods", namespace=workload.ref.namespace, selector=_selector_text(selector))
    ]


def _unhealthy_details(
    ctx: Context, pods: list[InventoryItem], namespace: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """The events and previous logs of up to three unhealthy pods."""
    pod_events: dict[str, list[dict[str, Any]]] = {}
    previous_logs: dict[str, str] = {}
    for p in [p for p in pods if _unhealthy(p.summary)][:3]:
        pod_events[p.ref.name] = [
            _event_row(e) for e in ctx.cluster.events(namespace=namespace, involved=p.ref)
        ][-20:]
        for state in cast(list[dict[str, Any]], p.summary.get("container_states") or []):
            if not (state.get("last_terminated") or int(state.get("restarts") or 0) > 0):
                continue
            try:
                text = ctx.cluster.logs(
                    p.ref.name, namespace=namespace, container=str(state.get("name")), previous=True, tail=50
                )
            except NotFound:
                text = ""
            if text.strip():
                previous_logs[f"{p.ref.name}/{state.get('name')}"] = text
    return pod_events, previous_logs


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
    pods = _pods_of(ctx, kind, workload, resource)
    selector = cast(dict[str, str], workload.summary.get("selector") or {})
    owners: list[dict[str, Any]] = []
    if kind == "Deployment" and selector:
        owners = [
            item_of(rs).model_dump(mode="json")
            for rs in ctx.cluster.list(
                "replicasets", namespace=i.namespace, selector=_selector_text(selector)
            )
        ]
    events = [
        _event_row(e)
        for e in ctx.cluster.events(
            namespace=i.namespace, involved=ResourceRef(kind=kind, namespace=i.namespace, name=i.name)
        )
    ]
    pod_events, previous_logs = _unhealthy_details(ctx, pods, i.namespace)
    data = {
        "workload": _item_dump(workload),
        "pods": [_item_dump(p) for p in pods],
        "owner_chain": owners,
        "events": events[-50:],
        "unhealthy_pod_events": pod_events,
        "previous_logs": previous_logs,
    }
    ready = sum(1 for p in pods if p.summary.get("ready"))
    restarts = sum(int(p.summary.get("restarts") or 0) for p in pods)
    counts = f"{ready}/{len(pods)} pods ready, {len(pod_events)} unhealthy, {restarts} restarts"
    return Result(summary=f"{kind} {i.name}: {counts}", data=data, coverage="observed")


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
_CHANGE_KINDS = (
    "deployments",
    "statefulsets",
    "daemonsets",
    "services",
    "configmaps",
    "ingresses",
    "networkpolicies",
    "cronjobs",
    "jobs",
)


def _revision_changes(
    ctx: Context, namespace: str, start: datetime, name: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rs in ctx.cluster.list("replicasets", namespace=namespace):
        item = item_of(rs)
        about = not name or (item.owner is not None and item.owner.name == name) or item.ref.name == name
        if item.created_at and item.created_at >= start and about:
            containers = cast(list[dict[str, Any]], item.summary.get("containers") or [])
            out.append(
                {
                    "time": item.created_at.isoformat(),
                    "kind": "revision",
                    "resource": f"ReplicaSet/{item.ref.name}",
                    "owner": item.owner.model_dump() if item.owner else None,
                    "revision": item.summary.get("revision"),
                    "change_cause": item.summary.get("change_cause"),
                    "images": [c.get("image") for c in containers],
                    "replicas": item.summary.get("replicas"),
                }
            )
    return out


def _resource_changes(
    ctx: Context, namespace: str, start: datetime, name: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind in _CHANGE_KINDS:
        for resource in ctx.cluster.list(kind, namespace=namespace):
            item = item_of(resource)
            if name and item.ref.name != name:
                continue
            label = f"{item.ref.kind}/{item.ref.name}"
            if item.created_at and item.created_at >= start:
                out.append({"time": item.created_at.isoformat(), "kind": "created", "resource": label})
            gen, seen = item.summary.get("generation"), item.summary.get("observed_generation")
            if gen is not None and seen is not None and gen != seen:
                out.append(
                    {
                        "time": None,
                        "kind": "rollout_in_progress",
                        "resource": label,
                        "generation": gen,
                        "observed_generation": seen,
                    }
                )
    return out


def _event_changes(ctx: Context, namespace: str, start: datetime, name: str | None) -> list[dict[str, Any]]:
    rows = _since([_event_row(e) for e in ctx.cluster.events(namespace=namespace)], start)
    return [
        {
            "time": r["time"],
            "kind": "event",
            "resource": r["object"],
            "reason": r["reason"],
            "message": r["message"],
        }
        for r in rows
        if r["reason"] in _CHANGE_REASONS and (not name or name in str(r["object"]))
    ]


def run_changes(inputs: BaseModel, ctx: Context) -> Result:
    i = cast(ChangesInputs, inputs)
    start = now() - timedelta(hours=i.since_hours)
    event_changes = _event_changes(ctx, i.namespace, start, i.name)
    changes = [
        *_revision_changes(ctx, i.namespace, start, i.name),
        *_resource_changes(ctx, i.namespace, start, i.name),
        *event_changes,
    ]
    changes.sort(key=lambda c: c["time"] or "")
    reason = f"{len(changes)} changes since {start.isoformat(timespec='minutes')}"
    coverage = "observed"
    if not event_changes and i.since_hours > 1:
        coverage = "observed" if changes else "no_data"
        reason += f"; {EVENTS_NOTE}, so older changes are seen only through revisions and creation times"
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
    checked = ctx.environment.connected_at.isoformat() if ctx.environment.connected_at else None
    summary = f"{len(matrix) - len(denied)} of {len(matrix)} permissions allowed" + (
        f"; denied: {', '.join(denied)}" if denied else ""
    )
    return Result(
        summary=summary,
        data={"permissions": matrix, "denied": denied, "checked_at": checked},
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
    INSPECT(
        id="k8s.inventory",
        title="Inventory of a scope",
        inputs=InventoryInputs,
        run=run_inventory,
        permissions=LIST_ALL,
        coverage=("observed", "no_data"),
        purpose=(
            "What exists in the scope and how it relates: workloads, pods, services, ingresses, policies, "
            "volumes, autoscalers, budgets, config names; relationships marked defined, observed or "
            "inferred. Start here for questions about a whole namespace."
        ),
    ),
    INSPECT(
        id="k8s.get",
        title="One resource",
        inputs=GetInputs,
        run=run_get,
        permissions=(("get", "pods"),),
        coverage=("observed", "observed_absent"),
        purpose=(
            "One resource as the cluster returns it, with a summary of the fields that matter. "
            "Secrets: metadata only, never the body."
        ),
    ),
    INSPECT(
        id="k8s.list",
        title="Resources of a kind",
        inputs=ListInputs,
        run=run_list,
        permissions=(("list", "pods"),),
        coverage=("observed", "observed_absent"),
        purpose="Resources of one kind in a scope, with a label or field selector; summaries, not bodies.",
    ),
    INSPECT(
        id="k8s.events",
        title="Events",
        inputs=EventsInputs,
        run=run_events,
        permissions=(("list", "events"),),
        coverage=("observed", "observed_absent", "no_data"),
        purpose=(
            "Events in a namespace or about one resource, in a period. Events expire after about an hour, "
            "so an empty answer for a longer period is no_data, not absence."
        ),
    ),
    INSPECT(
        id="k8s.logs",
        title="Container logs",
        inputs=LogsInputs,
        run=run_logs,
        permissions=(("get", "pods/log"),),
        coverage=("observed", "no_data"),
        retention="short",
        purpose="The tail of a container's log, current or the previous (crashed) one, since a time or not.",
    ),
    INSPECT(
        id="k8s.workload_health",
        title="Health of a workload",
        inputs=WorkloadInputs,
        run=run_workload_health,
        permissions=(("get", "pods"), ("list", "pods"), ("list", "events"), ("get", "pods/log")),
        coverage=("observed", "observed_absent"),
        purpose=(
            "A workload's pods with their conditions, restarts, last terminations and waiting reasons; its "
            "owner chain; its events; the events and previous logs of up to three unhealthy pods. "
            "Use for 'why is X failing'."
        ),
    ),
    INSPECT(
        id="k8s.changes",
        title="What changed in a period",
        inputs=ChangesInputs,
        run=run_changes,
        permissions=(("list", "replicasets"), ("list", "deployments"), ("list", "events")),
        coverage=("observed", "no_data"),
        purpose=(
            "Revisions (ReplicaSets with their change cause and images), resources created, rollouts in "
            "progress and change events in a namespace since a time. Use for 'what changed before the "
            "problem'."
        ),
    ),
    INSPECT(
        id="k8s.permissions",
        title="What K8sHolmes may read",
        inputs=NoInputs,
        run=run_permissions,
        permissions=(),
        coverage=("observed",),
        purpose=(
            "The permission matrix of the environment from the last connection check: which verbs on which "
            "kinds the cluster allows."
        ),
    ),
    INSPECT(
        id="k8s.top",
        title="Current CPU and memory",
        inputs=TopInputs,
        run=run_top,
        permissions=(("list", "pods.metrics.k8s.io"),),
        coverage=("observed", "cannot_answer"),
        retention="short",
        purpose="Current CPU and memory of pods or nodes from the metrics server, when one exists.",
    ),
]

__all__ = ["CAPABILITIES", "INSPECT", "K8S", "WORKLOAD_KINDS"]
