"""Relationships between inventory items, each with its basis (design §4.3). Code, no model."""

from __future__ import annotations

import re
from typing import Any, cast

from k8sholmes.inventory import WORKLOAD_KINDS, dig
from k8sholmes.records import InventoryItem, Relationship, ResourceRef


def _ref(kind: str, namespace: str | None, name: str) -> ResourceRef:
    return ResourceRef(kind=kind, namespace=namespace, name=name)


def _selector_matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def build_relationships(items: list[InventoryItem]) -> list[Relationship]:
    """Every relationship the inventory supports: defined from configuration, observed from endpoints,
    inferred from names in arguments and environment values."""
    by_kind: dict[str, list[InventoryItem]] = {}
    for item in items:
        by_kind.setdefault(item.ref.kind, []).append(item)
    out: list[Relationship] = []
    out += _ownership(items)
    out += _service_selection(by_kind)
    out += _ingress_routes(by_kind)
    out += _references(items)
    out += _targets(by_kind)
    out += _policies(by_kind)
    out += _endpoints(by_kind)
    out += _mentions(by_kind)
    return _unique(out)


def _unique(relationships: list[Relationship]) -> list[Relationship]:
    seen: set[str] = set()
    out: list[Relationship] = []
    for r in relationships:
        key = f"{r.source.key()}>{r.kind}>{r.target.key()}>{r.basis}"
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _ownership(items: list[InventoryItem]) -> list[Relationship]:
    return [
        Relationship(
            source=item.owner, target=item.ref, kind="owns", basis="defined", detail="ownerReferences"
        )
        for item in items
        if item.owner is not None
    ]


def _service_selection(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    out: list[Relationship] = []
    for svc in by_kind.get("Service", []):
        selector = cast(dict[str, str], svc.summary.get("selector") or {})
        for pod in by_kind.get("Pod", []):
            if pod.ref.namespace == svc.ref.namespace and _selector_matches(selector, pod.labels):
                out.append(
                    Relationship(
                        source=svc.ref,
                        target=pod.ref,
                        kind="selects",
                        basis="defined",
                        detail="spec.selector matches the pod's labels",
                    )
                )
        for kind in WORKLOAD_KINDS:
            for wl in by_kind.get(kind, []):
                pod_labels = cast(dict[str, str], wl.summary.get("pod_labels") or {})
                if wl.ref.namespace == svc.ref.namespace and _selector_matches(selector, pod_labels):
                    out.append(
                        Relationship(
                            source=svc.ref,
                            target=wl.ref,
                            kind="selects",
                            basis="defined",
                            detail="spec.selector matches the pod template's labels",
                        )
                    )
    return out


def _ingress_routes(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    out: list[Relationship] = []
    for ing in by_kind.get("Ingress", []):
        for backend in cast(list[dict[str, Any]], ing.summary.get("backends") or []):
            name = backend.get("service")
            if name:
                out.append(
                    Relationship(
                        source=ing.ref,
                        target=_ref("Service", ing.ref.namespace, str(name)),
                        kind="routes_to",
                        basis="defined",
                        detail=f"backend {backend.get('host') or ''}{backend.get('path') or ''}".strip(),
                    )
                )
    return out


def _references(items: list[InventoryItem]) -> list[Relationship]:
    """ConfigMaps, Secrets and PVCs a pod or a workload's template names."""
    out: list[Relationship] = []
    for item in items:
        if item.ref.kind == "Pod" or item.ref.kind in WORKLOAD_KINDS:
            out += _volume_references(item)
            for container in cast(list[dict[str, Any]], item.summary.get("containers") or []):
                out += _env_references(item, container)
    return out


def _named(item: InventoryItem, kind: str, name: Any, relation: str, detail: str) -> Relationship:
    target = _ref(kind, item.ref.namespace, str(name))
    return Relationship(source=item.ref, target=target, kind=relation, basis="defined", detail=detail)  # type: ignore[arg-type]


def _volume_references(item: InventoryItem) -> list[Relationship]:
    out: list[Relationship] = []
    for volume in cast(list[dict[str, Any]], item.summary.get("volumes") or []):
        label = f"volume {volume.get('name')}"
        if claim := dig(volume, "persistentVolumeClaim", "claimName"):
            out.append(_named(item, "PersistentVolumeClaim", claim, "mounts", label))
        if cm := dig(volume, "configMap", "name"):
            out.append(_named(item, "ConfigMap", cm, "references", label))
        if secret := dig(volume, "secret", "secretName"):
            out.append(_named(item, "Secret", secret, "references", f"{label} (name only)"))
    return out


def _env_references(item: InventoryItem, container: dict[str, Any]) -> list[Relationship]:
    out: list[Relationship] = []
    for env in cast(list[dict[str, Any]], container.get("env") or []):
        label = f"env {env.get('name')}"
        if cm := dig(env, "valueFrom", "configMapKeyRef", "name"):
            out.append(_named(item, "ConfigMap", cm, "references", label))
        if secret := dig(env, "valueFrom", "secretKeyRef", "name"):
            out.append(_named(item, "Secret", secret, "references", f"{label} (name only)"))
    for env_from in cast(list[dict[str, Any]], container.get("envFrom") or []):
        if cm := dig(env_from, "configMapRef", "name"):
            out.append(_named(item, "ConfigMap", cm, "references", "envFrom"))
        if secret := dig(env_from, "secretRef", "name"):
            out.append(_named(item, "Secret", secret, "references", "envFrom (name only)"))
    return out


def _targets(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    out: list[Relationship] = []
    for hpa in by_kind.get("HorizontalPodAutoscaler", []):
        target = cast(dict[str, Any], hpa.summary.get("target") or {})
        if target.get("name"):
            out.append(
                Relationship(
                    source=hpa.ref,
                    target=_ref(
                        str(target.get("kind", "Deployment")), hpa.ref.namespace, str(target["name"])
                    ),
                    kind="targets",
                    basis="defined",
                    detail="scaleTargetRef",
                )
            )
    for pdb in by_kind.get("PodDisruptionBudget", []):
        selector = cast(dict[str, str], pdb.summary.get("selector") or {})
        for kind in WORKLOAD_KINDS:
            for wl in by_kind.get(kind, []):
                if wl.ref.namespace == pdb.ref.namespace and _selector_matches(
                    selector, cast(dict[str, str], wl.summary.get("pod_labels") or {})
                ):
                    out.append(
                        Relationship(
                            source=pdb.ref,
                            target=wl.ref,
                            kind="targets",
                            basis="defined",
                            detail="selector matches the pod template's labels",
                        )
                    )
    return out


def _policies(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    out: list[Relationship] = []
    for policy in by_kind.get("NetworkPolicy", []):
        selector = cast(dict[str, str], policy.summary.get("pod_selector") or {})
        selects_all = bool(policy.summary.get("selects_all"))
        for pod in by_kind.get("Pod", []):
            if pod.ref.namespace == policy.ref.namespace and (
                selects_all or _selector_matches(selector, pod.labels)
            ):
                out.append(
                    Relationship(
                        source=policy.ref,
                        target=pod.ref,
                        kind="policy_selects",
                        basis="defined",
                        detail="podSelector",
                    )
                )
    return out


def _endpoints(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    """What the EndpointSlices say the Services really point at: observed, not defined."""
    out: list[Relationship] = []
    for slice_ in by_kind.get("EndpointSlice", []):
        service = slice_.summary.get("service")
        if not service:
            continue
        svc_ref = _ref("Service", slice_.ref.namespace, str(service))
        for endpoint in cast(list[dict[str, Any]], slice_.summary.get("endpoints") or []):
            target = cast(dict[str, Any], endpoint.get("target") or {})
            if target.get("kind") == "Pod" and target.get("name"):
                ready = endpoint.get("ready")
                out.append(
                    Relationship(
                        source=svc_ref,
                        target=_ref("Pod", slice_.ref.namespace, str(target["name"])),
                        kind="selects",
                        basis="observed",
                        detail=f"endpoint ready={ready}",
                    )
                )
    return out


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{2,}")


def _mentions(by_kind: dict[str, list[InventoryItem]]) -> list[Relationship]:
    """A Service's name inside a container's arguments or plain environment values: inferred."""
    services = {(s.ref.namespace, s.ref.name): s.ref for s in by_kind.get("Service", [])}
    out: list[Relationship] = []
    for kind in WORKLOAD_KINDS:
        for wl in by_kind.get(kind, []):
            words: set[str] = set()
            for container in cast(list[dict[str, Any]], wl.summary.get("containers") or []):
                for arg in cast(list[str], container.get("args") or []):
                    words.update(_WORD.findall(str(arg)))
                for env in cast(list[dict[str, Any]], container.get("env") or []):
                    value = env.get("value")
                    if isinstance(value, str):
                        words.update(_WORD.findall(value))
            for word in sorted(words):
                host = word.split(".")[0]
                namespace = (
                    word.split(".")[1]
                    if "." in word and word.split(".")[1:2] != ["svc"]
                    else wl.ref.namespace
                )
                target = services.get((namespace, host)) or services.get((wl.ref.namespace, host))
                if target is not None and target.name != wl.ref.name and len(host) >= 3:
                    out.append(
                        Relationship(
                            source=wl.ref,
                            target=target,
                            kind="mentions",
                            basis="inferred",
                            detail=f"{word!r} in arguments or environment",
                        )
                    )
    return out


def dependents_of(ref: ResourceRef, relationships: list[Relationship]) -> dict[str, list[Relationship]]:
    """What points at `ref`, grouped by basis, for an impact question (design §4.3)."""
    grouped: dict[str, list[Relationship]] = {"defined": [], "observed": [], "inferred": []}
    for r in relationships:
        if r.target.key() == ref.key() and r.kind != "owns":
            grouped[r.basis].append(r)
    return grouped
