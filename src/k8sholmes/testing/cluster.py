"""`FakeCluster`: a `Cluster` answered from resources given as dicts; `RecordedCluster` loads a recording.

Both answer `get`, `list`, `events`, `logs`, `top`, `can_i`, `version` and `api_resources` like the real
adapter does, including the Secret rule (bodies are never returned) and label and field selectors.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from k8sholmes.cluster import canonical_kind
from k8sholmes.errors import InvalidInput, NotFound
from k8sholmes.records import ResourceRef

_SELECTOR_SET = re.compile(r"^\s*([A-Za-z0-9_./-]+)\s+(in|notin)\s*\(([^)]*)\)\s*$")


def matches_selector(labels: dict[str, str], selector: str | None) -> bool:
    """A label selector in kubectl's syntax: `k=v`, `k!=v`, `k`, `!k`, `k in (a,b)`, `k notin (a)`."""
    if not selector or not selector.strip():
        return True
    return all(
        _requirement_holds(labels, term.strip()) for term in _split_requirements(selector) if term.strip()
    )


def _requirement_holds(labels: dict[str, str], term: str) -> bool:
    m = _SELECTOR_SET.match(term)
    if m:
        key, op, values = m.group(1), m.group(2), {v.strip() for v in m.group(3).split(",")}
        return (labels.get(key) in values) == (op == "in")
    if "!=" in term:
        key, value = term.split("!=", 1)
        return labels.get(key.strip()) != value.strip()
    if "=" in term:
        key, value = term.split("==", 1) if "==" in term else term.split("=", 1)
        return labels.get(key.strip()) == value.strip()
    if term.startswith("!"):
        return term[1:].strip() not in labels
    return term in labels


def _split_requirements(selector: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in selector:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def _lookup(obj: Any, path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = cast(dict[str, Any], value).get(part)
    return value


def matches_fields(obj: dict[str, Any], field_selector: str | None) -> bool:
    """A field selector: `metadata.name=x,involvedObject.kind!=Pod`."""
    if not field_selector:
        return True
    for raw in field_selector.split(","):
        term = raw.strip()
        if not term:
            continue
        if "!=" in term:
            path, value = term.split("!=", 1)
            if str(_lookup(obj, path.strip())) == value.strip():
                return False
        else:
            path, value = term.split("==", 1) if "==" in term else term.split("=", 1)
            if str(_lookup(obj, path.strip())) != value.strip():
                return False
    return True


def _without_secret_body(resource: dict[str, Any]) -> dict[str, Any]:
    if canonical_kind(str(resource.get("kind", ""))) != "Secret":
        return resource
    return {"kind": "Secret", "metadata": dict(resource.get("metadata", {})), "data": "<never fetched>"}


def _event_time(event: dict[str, Any]) -> str:
    for key in ("lastTimestamp", "eventTime", "firstTimestamp"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    series = event.get("series")
    if isinstance(series, dict):
        last = series.get("lastObservedTime")  # type: ignore[reportUnknownMemberType]
        if isinstance(last, str):
            return last
    created = _lookup(event, "metadata.creationTimestamp")
    return str(created) if created else ""


class FakeCluster:
    """A `Cluster` over resources in memory. Every call is appended to `calls` for assertions."""

    def __init__(
        self,
        resources: Iterable[dict[str, Any]] = (),
        *,
        events: Iterable[dict[str, Any]] = (),
        logs: dict[str, str] | None = None,
        top: dict[str, list[dict[str, Any]] | None] | None = None,
        can_i: dict[str, bool] | None = None,
        default_allowed: bool = True,
        version: dict[str, Any] | None = None,
        api_resources: Iterable[str] | None = None,
        context: str = "fake",
    ) -> None:
        self.context = context
        self.resources: list[dict[str, Any]] = []
        for r in resources:
            self.add(r)
        self._events: list[dict[str, Any]] = list(events)
        self._logs: dict[str, str] = dict(logs or {})
        self._top: dict[str, list[dict[str, Any]] | None] = dict(top or {})
        self._can_i: dict[str, bool] = dict(can_i or {})
        self.default_allowed = default_allowed
        self._version = version or {
            "clientVersion": {"gitVersion": "v1.36.0"},
            "serverVersion": {"gitVersion": "v1.36.0"},
        }
        self._api_resources = list(api_resources) if api_resources is not None else None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # building

    def add(self, resource: dict[str, Any]) -> None:
        if "kind" not in resource or "metadata" not in resource or "name" not in resource["metadata"]:
            raise InvalidInput("a resource needs kind and metadata.name")
        resource["kind"] = canonical_kind(str(resource["kind"]))
        self.resources.append(resource)

    def add_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    def set_logs(
        self, namespace: str, pod: str, text: str, *, container: str | None = None, previous: bool = False
    ) -> None:
        self._logs[self._log_key(namespace, pod, container, previous)] = text

    @staticmethod
    def _log_key(namespace: str, pod: str, container: str | None, previous: bool) -> str:
        return f"{namespace}__{pod}__{container or '-'}{'__previous' if previous else ''}"

    # the port

    def get(self, kind: str, name: str, *, namespace: str | None = None) -> dict[str, Any]:
        self.calls.append(("get", {"kind": kind, "name": name, "namespace": namespace}))
        wanted = canonical_kind(kind)
        for r in self.resources:
            meta = r.get("metadata", {})
            if (
                r["kind"] == wanted
                and meta.get("name") == name
                and (namespace is None or meta.get("namespace") == namespace)
            ):
                return _without_secret_body(r)
        where = f" in namespace {namespace}" if namespace else ""
        raise NotFound(f'{wanted} "{name}" not found{where}')

    def list(
        self,
        kind: str,
        *,
        namespace: str | None = None,
        selector: str | None = None,
        field_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("list", {"kind": kind, "namespace": namespace, "selector": selector, "fields": field_selector})
        )
        wanted = canonical_kind(kind)
        if wanted == "Event":
            return self.events(namespace=namespace)
        found = [
            _without_secret_body(r)
            for r in self.resources
            if r["kind"] == wanted
            and (namespace is None or r.get("metadata", {}).get("namespace") == namespace)
            and matches_selector(dict(r.get("metadata", {}).get("labels", {}) or {}), selector)
            and matches_fields(r, field_selector)
        ]
        return sorted(found, key=lambda r: (r["metadata"].get("namespace") or "", r["metadata"]["name"]))

    def events(
        self, *, namespace: str | None = None, involved: ResourceRef | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("events", {"namespace": namespace, "involved": involved.key() if involved else None})
        )
        found = [
            e
            for e in self._events
            if (namespace is None or e.get("metadata", {}).get("namespace") == namespace)
            and (
                involved is None
                or (
                    _lookup(e, "involvedObject.name") == involved.name
                    and canonical_kind(str(_lookup(e, "involvedObject.kind") or ""))
                    == canonical_kind(involved.kind)
                )
            )
        ]
        return sorted(found, key=_event_time)

    def logs(
        self,
        pod: str,
        *,
        namespace: str,
        container: str | None = None,
        previous: bool = False,
        tail: int = 200,
        since: datetime | None = None,
    ) -> str:
        self.calls.append(
            ("logs", {"pod": pod, "namespace": namespace, "container": container, "previous": previous})
        )
        key = self._log_key(namespace, pod, container, previous)
        text = self._logs.get(key)
        if text is None and container is None:
            candidates = [
                v
                for k, v in self._logs.items()
                if k.startswith(f"{namespace}__{pod}__") and k.endswith("__previous") == previous
            ]
            text = candidates[0] if candidates else None
        if text is None:
            if not any(
                r["kind"] == "Pod"
                and r["metadata"].get("name") == pod
                and r["metadata"].get("namespace") == namespace
                for r in self.resources
            ):
                raise NotFound(f'pods "{pod}" not found in namespace {namespace}')
            text = ""
        lines = text.splitlines(keepends=True)
        if since is not None:
            stamp = since.strftime("%Y-%m-%dT%H:%M:%S")
            lines = [ln for ln in lines if ln[:19] >= stamp]
        return "".join(lines[-tail:])

    def top(self, kind: str, *, namespace: str | None = None) -> list[dict[str, Any]] | None:
        self.calls.append(("top", {"kind": kind, "namespace": namespace}))
        key = "pods" if kind.lower().startswith("pod") else "nodes"
        rows = self._top.get(key)
        if rows is None:
            return None
        if key == "pods" and namespace is not None:
            return [r for r in rows if r.get("namespace") == namespace]
        return list(rows)

    def can_i(self, verb: str, kind: str, *, namespace: str | None = None) -> bool:
        self.calls.append(("can_i", {"verb": verb, "kind": kind, "namespace": namespace}))
        for key in (f"{verb} {kind} {namespace or '*'}", f"{verb} {kind} *", f"{verb} * *"):
            if key in self._can_i:
                return self._can_i[key]
        return self.default_allowed

    def version(self) -> dict[str, Any]:
        self.calls.append(("version", {}))
        return dict(self._version)

    def api_resources(self) -> list[str]:
        self.calls.append(("api_resources", {}))
        if self._api_resources is not None:
            return list(self._api_resources)
        return sorted({r["kind"].lower() + "s" for r in self.resources} | {"events", "nodes", "namespaces"})

    # recordings (the layout `Kubectl(record=…)` writes)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for r in self.resources:
            by_kind.setdefault(r["kind"], []).append(_without_secret_body(r))
        (folder / "resources").mkdir(exist_ok=True)
        for kind, items in by_kind.items():
            (folder / "resources" / f"{kind}.json").write_text(json.dumps(items, indent=1, default=str))
        (folder / "events.json").write_text(json.dumps(self._events, indent=1, default=str))
        (folder / "logs").mkdir(exist_ok=True)
        for key, text in self._logs.items():
            (folder / "logs" / f"{key}.log").write_text(text)
        (folder / "top").mkdir(exist_ok=True)
        for key, rows in self._top.items():
            (folder / "top" / f"{key}.json").write_text(json.dumps(rows, indent=1))
        (folder / "can_i.json").write_text(json.dumps(self._can_i, indent=1, sort_keys=True))
        (folder / "version.json").write_text(json.dumps(self._version, indent=1))
        (folder / "api_resources.json").write_text(json.dumps(self.api_resources(), indent=1))
        (folder / "context.txt").write_text(self.context)


def RecordedCluster(folder: Path | str) -> FakeCluster:  # noqa: N802 - reads as a class to users
    """A `Cluster` replayed from a recording folder (written by `Kubectl(record=…)` or `FakeCluster.save`)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotFound(f"recording folder {folder} does not exist")
    resources: list[dict[str, Any]] = []
    for path in sorted((folder / "resources").glob("*.json")) if (folder / "resources").is_dir() else []:
        resources.extend(json.loads(path.read_text()))
    events_path = folder / "events.json"
    events: list[dict[str, Any]] = json.loads(events_path.read_text()) if events_path.exists() else []
    logs = (
        {p.stem: p.read_text() for p in (folder / "logs").glob("*.log")} if (folder / "logs").is_dir() else {}
    )
    top: dict[str, list[dict[str, Any]] | None] = {}
    if (folder / "top").is_dir():
        for p in (folder / "top").glob("*.json"):
            top[
                p.stem if p.stem in ("pods", "nodes") else ("pods" if p.stem.startswith("pod") else "nodes")
            ] = json.loads(p.read_text())
    can_i_path = folder / "can_i.json"
    can_i: dict[str, bool] = json.loads(can_i_path.read_text()) if can_i_path.exists() else {}
    version_path = folder / "version.json"
    version = json.loads(version_path.read_text()) if version_path.exists() else None
    api_path = folder / "api_resources.json"
    api_resources = json.loads(api_path.read_text()) if api_path.exists() else None
    context_path = folder / "context.txt"
    context = context_path.read_text().strip() if context_path.exists() else folder.name
    return FakeCluster(
        resources,
        events=events,
        logs=logs,
        top=top,
        can_i=can_i,
        version=version,
        api_resources=api_resources,
        context=context,
    )


def check_cluster(cluster: Any, *, namespace: str, secret_name: str | None = None) -> None:
    """The contract every `Cluster` keeps (design §3 of the first record, §5.3): raises AssertionError."""
    try:
        cluster.get("pod", "this-pod-does-not-exist-9f3a", namespace=namespace)
    except NotFound:
        pass
    else:
        raise AssertionError("get of a missing pod must raise NotFound")
    pods = cluster.list("pods", namespace=namespace)
    assert isinstance(pods, list), "list returns a list"
    events = cluster.events(namespace=namespace)
    times = [_event_time(e) for e in events]
    assert times == sorted(times), "events are sorted by time"
    for secret in cluster.list("secrets", namespace=namespace):
        assert not isinstance(secret.get("data"), dict), "a Secret body is never returned"
        assert "stringData" not in secret, "a Secret body is never returned"
    if secret_name:
        secret = cluster.get("secret", secret_name, namespace=namespace)
        assert not isinstance(secret.get("data"), dict), "a Secret body is never returned"
    top = cluster.top("pods", namespace=namespace)
    assert top is None or isinstance(top, list), "top is a list or None without metrics"
    assert isinstance(cluster.can_i("get", "pods", namespace=namespace), bool)
    assert "clientVersion" in cluster.version() or "serverVersion" in cluster.version()
    assert isinstance(cluster.api_resources(), list)
