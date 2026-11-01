"""The `Cluster` port and the `kubectl` adapter (design §5.3, D-003, D-010).

The adapter is the only place `kubectl` is started: argument lists, never a shell, a timeout on every
call, `--context` explicit, `-o json`, and only the read-only verbs in `ALLOWED_VERBS`. It never fetches a
Secret's body: Secrets are listed and checked by name only. With `record=`, every answer is also written
to a recording folder that `k8sholmes.testing.RecordedCluster` replays.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from k8sholmes.errors import ClusterUnreachable, InvalidInput, NotFound, PermissionRefused, ToolMissing
from k8sholmes.records import ResourceRef

ALLOWED_VERBS = frozenset({"get", "logs", "top", "auth", "version", "api-resources"})

# Kind -> (plural, singular, short names): kubectl's names for each kind. The plural comes first.
KIND_TABLE: dict[str, tuple[str, ...]] = {
    "Pod": ("pods", "pod", "po"),
    "Deployment": ("deployments", "deployment", "deploy"),
    "ReplicaSet": ("replicasets", "replicaset", "rs"),
    "StatefulSet": ("statefulsets", "statefulset", "sts"),
    "DaemonSet": ("daemonsets", "daemonset", "ds"),
    "Job": ("jobs", "job"),
    "CronJob": ("cronjobs", "cronjob", "cj"),
    "Service": ("services", "service", "svc"),
    "Endpoints": ("endpoints", "ep"),
    "EndpointSlice": ("endpointslices", "endpointslice"),
    "Ingress": ("ingresses", "ingress", "ing"),
    "NetworkPolicy": ("networkpolicies", "networkpolicy", "netpol"),
    "PersistentVolumeClaim": ("persistentvolumeclaims", "persistentvolumeclaim", "pvc"),
    "PersistentVolume": ("persistentvolumes", "persistentvolume", "pv"),
    "StorageClass": ("storageclasses", "storageclass", "sc"),
    "HorizontalPodAutoscaler": ("horizontalpodautoscalers", "horizontalpodautoscaler", "hpa"),
    "PodDisruptionBudget": ("poddisruptionbudgets", "poddisruptionbudget", "pdb"),
    "ServiceAccount": ("serviceaccounts", "serviceaccount", "sa"),
    "ConfigMap": ("configmaps", "configmap", "cm"),
    "Secret": ("secrets", "secret"),
    "Node": ("nodes", "node", "no"),
    "Namespace": ("namespaces", "namespace", "ns"),
    "Event": ("events", "event", "ev"),
    "PrometheusRule": ("prometheusrules", "prometheusrule"),
    "ServiceMonitor": ("servicemonitors", "servicemonitor"),
}
_ALIASES: dict[str, str] = {alias: kind for kind, aliases in KIND_TABLE.items() for alias in aliases}


def canonical_kind(kind: str) -> str:
    """`deploy`, `deployments` and `Deployment` are one kind."""
    key = kind.partition(".")[0].lower()
    return _ALIASES.get(key, kind[0].upper() + kind[1:])


def plural_of(kind: str) -> str:
    """The plural kubectl lists a kind by (`ingresses`, `networkpolicies`)."""
    return KIND_TABLE.get(kind, (kind.lower() + "s",))[0]


class Cluster(Protocol):
    """Read-only access to one cluster. Every method is a question; none changes anything."""

    context: str

    def get(self, kind: str, name: str, *, namespace: str | None = None) -> dict[str, Any]: ...
    def list(
        self,
        kind: str,
        *,
        namespace: str | None = None,
        selector: str | None = None,
        field_selector: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def events(
        self, *, namespace: str | None = None, involved: ResourceRef | None = None
    ) -> list[dict[str, Any]]: ...
    def logs(
        self,
        pod: str,
        *,
        namespace: str,
        container: str | None = None,
        previous: bool = False,
        tail: int = 200,
        since: datetime | None = None,
    ) -> str: ...
    def top(self, kind: str, *, namespace: str | None = None) -> list[dict[str, Any]] | None: ...
    def can_i(self, verb: str, kind: str, *, namespace: str | None = None) -> bool: ...
    def version(self) -> dict[str, Any]: ...
    def api_resources(self) -> list[str]: ...


def _is_secret(kind: str) -> bool:
    return canonical_kind(kind) == "Secret"


def _check_name(value: str, what: str) -> None:
    if not value or value.startswith("-") or "," in value or any(c.isspace() for c in value):
        raise InvalidInput(f"{what} {value!r} is not a valid Kubernetes name")


class Kubectl:
    """The real adapter. `context` is the kubeconfig context; `kubeconfig` a path or None for the default."""

    def __init__(
        self,
        context: str,
        *,
        kubeconfig: str | None = None,
        timeout_ms: int = 20_000,
        record: Path | None = None,
        executable: str = "kubectl",
    ) -> None:
        self.context = context
        self.kubeconfig = kubeconfig
        self.timeout_ms = timeout_ms
        self.record = record
        self.executable = executable

    # running

    def _run(self, args: list[str], *, ok_codes: tuple[int, ...] = (0,)) -> str:
        verb = args[0]
        if verb not in ALLOWED_VERBS:
            raise PermissionRefused(f"kubectl {verb} is not a read-only verb K8sHolmes may run (design §5.3)")
        command = [
            self.executable,
            "--context",
            self.context,
            "--request-timeout",
            f"{self.timeout_ms // 1000}s",
        ]
        if self.kubeconfig:
            command += ["--kubeconfig", self.kubeconfig]
        command += args
        if shutil.which(self.executable) is None:
            raise ToolMissing(f"{self.executable} is not on the PATH; install kubectl and try again")
        try:
            done = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, timeout=self.timeout_ms / 1000, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise ClusterUnreachable(
                f"kubectl {verb} timed out after {self.timeout_ms} ms (context {self.context})"
            ) from exc
        if done.returncode not in ok_codes:
            raise self._error(verb, done.stderr.strip())
        return done.stdout

    def _error(self, verb: str, stderr: str) -> Exception:
        lower = stderr.lower()
        if "notfound" in lower or "not found" in lower:
            return NotFound(stderr or f"kubectl {verb}: not found")
        if "forbidden" in lower:
            return PermissionRefused(f"the cluster refused kubectl {verb}: {stderr}")
        return ClusterUnreachable(
            f"kubectl {verb} failed for context {self.context}: {stderr or 'no output'}"
        )

    def _json(self, args: list[str]) -> Any:
        out = self._run([*args, "-o", "json"])
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise ClusterUnreachable(f"kubectl {args[0]} returned something that is not JSON") from exc

    @staticmethod
    def _ns(namespace: str | None) -> list[str]:
        if namespace is None:
            return ["--all-namespaces"]
        _check_name(namespace, "namespace")
        return ["--namespace", namespace]

    # the port

    def get(self, kind: str, name: str, *, namespace: str | None = None) -> dict[str, Any]:
        _check_name(kind, "kind")
        _check_name(name, "name")
        ns = ["--namespace", namespace] if namespace else []
        if _is_secret(kind):
            out = self._run(["get", "secret", name, *ns, "-o", "name"]).strip()
            if not out:
                raise NotFound(f"secret {name!r} does not exist")
            found = {
                "kind": "Secret",
                "metadata": {"name": name, "namespace": namespace},
                "data": "<never fetched>",
            }
            self._record("resources", "Secret", [found])
            return found
        found = self._json(["get", kind, name, *ns])
        self._record("resources", found.get("kind", kind), [found])
        return found

    def list(
        self,
        kind: str,
        *,
        namespace: str | None = None,
        selector: str | None = None,
        field_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        _check_name(kind, "kind")
        args = ["get", kind, *self._ns(namespace)]
        if selector:
            args += ["--selector", selector]
        if field_selector:
            args += ["--field-selector", field_selector]
        if _is_secret(kind):
            template = '{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"\\n"}{end}'
            out = self._run([*args, "-o", f"jsonpath={template}"])
            items = [
                {"kind": "Secret", "metadata": {"namespace": ns, "name": name}, "data": "<never fetched>"}
                for ns, _, name in (line.partition("/") for line in out.splitlines() if line.strip())
            ]
        else:
            items = list(self._json(args).get("items", []))
        if items:
            self._record("resources", str(items[0].get("kind") or kind), items)
        return items

    def events(
        self, *, namespace: str | None = None, involved: ResourceRef | None = None
    ) -> list[dict[str, Any]]:
        args = ["get", "events", *self._ns(namespace)]
        if involved is not None:
            args += [
                "--field-selector",
                f"involvedObject.name={involved.name},involvedObject.kind={involved.kind}",
            ]
        items = list(self._json(args).get("items", []))
        self._record("events", None, items)
        return items

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
        _check_name(pod, "pod")
        args = ["logs", pod, "--namespace", namespace, "--tail", str(int(tail)), "--timestamps"]
        if container:
            _check_name(container, "container")
            args += ["--container", container]
        if previous:
            args.append("--previous")
        if since is not None:
            args += ["--since-time", since.strftime("%Y-%m-%dT%H:%M:%SZ")]
        text = self._run(args)
        self._record_log(namespace, pod, container, previous, text)
        return text

    def top(self, kind: str, *, namespace: str | None = None) -> list[dict[str, Any]] | None:
        kind = kind.lower()
        if kind not in {"pods", "pod", "nodes", "node"}:
            raise InvalidInput("top knows pods and nodes")
        args = ["top", kind, "--no-headers"]
        if kind.startswith("pod"):
            args += self._ns(namespace)
        try:
            out = self._run(args)
        except ClusterUnreachable as exc:
            if "metrics" in str(exc).lower():
                return None
            raise
        rows = [line.split() for line in out.splitlines() if line.strip()]
        if kind.startswith("pod"):
            if namespace is None:
                items = [
                    {"namespace": r[0], "name": r[1], "cpu": r[2], "memory": r[3]}
                    for r in rows
                    if len(r) >= 4
                ]
            else:
                items = [
                    {"namespace": namespace, "name": r[0], "cpu": r[1], "memory": r[2]}
                    for r in rows
                    if len(r) >= 3
                ]
        else:
            items = [
                {"name": r[0], "cpu": r[1], "cpu_percent": r[2], "memory": r[3], "memory_percent": r[4]}
                for r in rows
                if len(r) >= 5
            ]
        self._record("top", kind, items)
        return items

    def can_i(self, verb: str, kind: str, *, namespace: str | None = None) -> bool:
        _check_name(verb, "verb")
        _check_name(kind, "kind")
        args = ["auth", "can-i", verb, kind]
        if namespace:
            args += ["--namespace", namespace]
        answer = self._run(args, ok_codes=(0, 1)).strip().lower()  # "no" exits 1
        if answer not in ("yes", "no"):
            raise ClusterUnreachable(f"kubectl auth can-i answered {answer!r} for context {self.context}")
        allowed = answer == "yes"
        self._record_can_i(verb, kind, namespace, allowed)
        return allowed

    def version(self) -> dict[str, Any]:
        data = self._json(["version"])
        self._record("version", None, data)
        return data

    def api_resources(self) -> list[str]:
        out = self._run(["api-resources", "--verbs=list", "-o", "name"])
        kinds = [line.strip() for line in out.splitlines() if line.strip()]
        self._record("api_resources", None, kinds)
        return kinds

    # recording (the folder layout is read by k8sholmes.testing.RecordedCluster)

    def _record(self, section: str, kind: str | None, data: Any) -> None:
        if self.record is None:
            return
        folder = self.record / section if kind else self.record
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (f"{kind}.json" if kind else f"{section}.json")
        if isinstance(data, list):
            old_items: list[Any] = json.loads(path.read_text()) if path.exists() else []
            new_items: list[Any] = list(cast(list[Any], data))
            seen = {json.dumps(e, sort_keys=True, default=str) for e in old_items}
            merged = old_items + [
                d for d in new_items if json.dumps(d, sort_keys=True, default=str) not in seen
            ]
            path.write_text(json.dumps(merged, indent=1, default=str))
        else:
            path.write_text(json.dumps(data, indent=1, default=str))

    def _record_log(self, namespace: str, pod: str, container: str | None, previous: bool, text: str) -> None:
        if self.record is None:
            return
        folder = self.record / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{namespace}__{pod}__{container or '-'}{'__previous' if previous else ''}.log"
        (folder / name).write_text(text)

    def _record_can_i(self, verb: str, kind: str, namespace: str | None, allowed: bool) -> None:
        if self.record is None:
            return
        path = self.record / "can_i.json"
        self.record.mkdir(parents=True, exist_ok=True)
        table: dict[str, bool] = json.loads(path.read_text()) if path.exists() else {}
        table[f"{verb} {kind} {namespace or '*'}"] = allowed
        path.write_text(json.dumps(table, indent=1, sort_keys=True))
