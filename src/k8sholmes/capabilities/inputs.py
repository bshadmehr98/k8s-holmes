"""Inputs a model can get right (change 0002, parts 2, 3 and 5; design §5.1).

Three helpers around a capability's pydantic input model, all deterministic code:

- `canonical`: the names models expect are accepted (`label_selector` for `selector`, `po` for `Pod`),
  and the registry keeps what the model sent when it differs;
- `Known` and `schema_for`: the kinds the cluster serves and its namespaces, shown to the model as allowed
  values and checked by validation (`unknown_values`);
- `explain`: an invalid call's message names each field, lists the accepted fields and suggests the
  closest field or value, as plain text the model can act on.

None of this is the gate: what may run is still decided in the registry (design §5.2).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from k8sholmes.cluster import KIND_TABLE, Cluster, canonical_kind, table_kind
from k8sholmes.errors import K8sHolmesError
from k8sholmes.inventory import dig
from k8sholmes.records import Environment, Inventory
from k8sholmes.store import Store

# Field names models use for this package's canonical ones (0002, part 5).
FIELD_ALIASES: dict[str, str] = {
    "label_selector": "selector",
    "labelSelector": "selector",
    "labels": "selector",
    "fieldSelector": "field_selector",
    "ns": "namespace",
    "resource_name": "name",
}
NOT_JSON = "(not a JSON object)"  # the key a structured fill that never parsed is sent under (0002)
MAX_ENUM = 200  # above this many namespaces the field stays free text; suggestions do the work


@dataclass(frozen=True)
class Known:
    """What exists in an environment; an empty set means "not known", and nothing is checked."""

    kinds: frozenset[str] = frozenset()  # resource names the cluster serves, lower case (`pods`)
    namespaces: frozenset[str] = frozenset()
    source: str = ""  # where the namespaces came from, for the message

    def kind_choices(self) -> list[str]:
        """The kinds as the model is shown them: the table's names first, then other served resources."""
        served = {canonical_kind(k) for k in self.kinds}
        table = [kind for kind in KIND_TABLE if kind in served]
        named = {a for kind in table for a in KIND_TABLE[kind]}
        return [*table, *sorted(k for k in self.kinds if k not in named)]

    def has_kind(self, kind: str) -> bool:
        """Whether the cluster serves `kind` by any name kubectl takes: plural, singular, Kind, group."""
        if not self.kinds:
            return True
        base = kind.partition(".")[0].lower()
        known = table_kind(base)
        if known is not None:
            return KIND_TABLE[known][0] in self.kinds
        return any(name in self.kinds for name in (base, base + "s", base + "es", base[:-1] + "ies"))


def known_for(store: Store, environment: Environment, cluster: Cluster | None = None) -> Known:
    """The served kinds from the last connection; the namespaces listed from the cluster when it allows
    that, else those of the latest inventory of every namespace if nothing in it was cut or unreadable."""
    kinds = frozenset(k.lower() for k in environment.kinds)
    if cluster is not None and environment.permissions.get("list namespaces"):
        try:
            names = {str(dig(n, "metadata", "name")) for n in cluster.list("namespaces")}
            return Known(kinds, frozenset(names), "listed at the start of this run")
        except K8sHolmesError:
            pass  # fall back to the inventory
    found = store.find(Inventory, {"environment_id": environment.id}, order_by="taken_at DESC", limit=20)
    whole = next((i for i in found if not i.namespaces), None)
    if whole is None or whole.truncated or whole.unreadable:
        return Known(kinds)  # a partial picture would refuse namespaces that exist
    names = {i.ref.namespace for i in whole.items if i.ref.namespace}
    return Known(kinds, frozenset(names), f"from the inventory of {whole.taken_at:%Y-%m-%d %H:%M} UTC")


def _known_fields(model: type[BaseModel]) -> dict[str, str]:
    return cast(dict[str, str], getattr(model, "known_fields", {}))


def canonical(model: type[BaseModel], raw: dict[str, Any]) -> dict[str, Any]:
    """`raw` with declared aliases renamed and kinds of the table in their canonical form; unknown fields
    stay, so validation still refuses them (guarantee 2). A kind with an API group is kept as sent:
    `pods.metrics.k8s.io` is not `Pod`."""
    fields = model.model_fields
    out: dict[str, Any] = {}
    for key, value in raw.items():
        target = FIELD_ALIASES.get(key, key)
        if target != key and (target not in fields or target in raw):
            target = key  # not an alias for this model, or the canonical name was sent as well
        out[target] = value
    for name, what in _known_fields(model).items():
        value = out.get(name)
        if what == "kinds" and isinstance(value, str) and "." not in value:
            out[name] = table_kind(value) or value
    return out


def unknown_values(
    model: type[BaseModel], raw: dict[str, Any], known: Known, allow: list[str]
) -> list[tuple[str, str]]:
    """Problems with values that are not in the environment: a kind it does not serve, a namespace it
    does not have. Suggestions stay within the namespace allow-list `allow` when there is one."""
    namespaces = sorted(known.namespaces & set(allow)) if allow else sorted(known.namespaces)
    problems: list[tuple[str, str]] = []
    for name, what in _known_fields(model).items():
        value = raw.get(name)
        values = cast(list[Any], value) if isinstance(value, list) else [value]
        for v in values:
            if not isinstance(v, str):
                continue
            if what == "kinds" and not known.has_kind(v):
                choices = known.kind_choices()
                problems.append((name, f"{v!r} is not a kind this cluster serves{_suggest(v, choices)}"))
            elif what == "namespaces" and known.namespaces and v not in known.namespaces:
                where = f" (namespaces {known.source})" if known.source else ""
                problems.append(
                    (name, f"{v!r} is not a namespace in this environment{_suggest(v, namespaces)}{where}")
                )
    return problems


def _suggest(value: str, choices: list[str]) -> str:
    close = difflib.get_close_matches(value, choices, n=2, cutoff=0.6)
    if not close:  # `label_sel` -> `selector`: a word of the value that starts a choice
        words = [w.lower() for w in re.split(r"[_.\-]|(?<=[a-z])(?=[A-Z])", value) if len(w) >= 3]
        close = [c for c in choices if any(c.lower().startswith(w) for w in words)][:2]
    return f"; did you mean {' or '.join(repr(c) for c in close)}?" if close else ""


def explain(capability_id: str, model: type[BaseModel], problems: list[tuple[str, str, bool]]) -> str:
    """The invalid call's message: each problem (field, message, whether the field is unknown) with a
    suggestion, then the accepted fields."""
    fields = list(model.model_fields)
    lines = [f"{capability_id}: invalid inputs"]
    for name, message, unknown_field in problems:
        if name == NOT_JSON:
            lines.append("- the inputs were not a JSON object")
            continue
        hint = ""
        if unknown_field:
            alias = FIELD_ALIASES.get(name)
            hint = f"; did you mean {alias!r}?" if alias in fields else _suggest(name, fields)
        lines.append(f"- {name}: {message}{hint}")
    accepted = [f"{n} (required)" if f.is_required() else n for n, f in model.model_fields.items()]
    lines.append("accepted fields: " + ", ".join(accepted))
    return "\n".join(lines)


def schema_for(model: type[BaseModel], known: Known | None, allow: list[str]) -> dict[str, Any]:
    """The JSON schema the model sees: the input model's, with the known kinds and namespaces as enums."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    if known is None:
        return schema
    props = cast(dict[str, dict[str, Any]], schema.get("properties", {}))
    namespaces = sorted(known.namespaces & set(allow)) if allow else sorted(known.namespaces)
    for name, what in _known_fields(model).items():
        choices = known.kind_choices() if what == "kinds" else namespaces
        if name in props and choices and len(choices) <= MAX_ENUM:
            _set_enum(props[name], choices)
    return schema


def _set_enum(prop: dict[str, Any], choices: list[str]) -> None:
    """An enum on a string field, a nullable string field or the items of a list of strings."""
    if prop.get("type") == "array":
        prop["items"] = {"type": "string", "enum": choices}
        return
    variants = cast(list[dict[str, Any]], prop.get("anyOf") or [])
    if variants:  # `str | None`: pydantic writes anyOf [string, null]
        for variant in variants:
            if variant.get("type") == "string":
                variant["enum"] = choices
        return
    prop["enum"] = choices
