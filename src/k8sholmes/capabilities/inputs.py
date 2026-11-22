"""Inputs a model can get right (change 0002, parts 2, 3 and 5; design §5.1).

Three helpers around a capability's pydantic input model, all deterministic code:

- `canonical`: the names models expect are accepted (`label_selector` for `selector`, `pod` for `Pod`),
  and what the model sent is kept when it differs;
- `Known` and `schema_for`: the kinds the cluster serves and the namespaces of the latest inventory, shown
  to the model as allowed values and checked by validation;
- `explain`: an invalid call's message names the field, lists the accepted fields and suggests the closest
  field or value, as plain text the model can act on.

None of this is the gate: what may run is still decided in the registry (design §5.2).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel

from k8sholmes.cluster import KIND_TABLE, canonical_kind
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
MAX_ENUM = 200  # above this many namespaces the field stays free text; suggestions do the work


@dataclass(frozen=True)
class Known:
    """What exists in an environment, as far as K8sHolmes knows; an empty set means "not known"."""

    kinds: frozenset[str] = frozenset()  # resource names the cluster serves (`pods`, `ingressroutes`)
    namespaces: frozenset[str] = frozenset()
    inventory_at: datetime | None = None
    _canonical: frozenset[str] = field(default=frozenset(), repr=False)

    @classmethod
    def of(cls, kinds: list[str], namespaces: set[str], inventory_at: datetime | None) -> Known:
        served = frozenset(k.lower() for k in kinds)
        return cls(
            kinds=served,
            namespaces=frozenset(namespaces),
            inventory_at=inventory_at,
            _canonical=frozenset(canonical_kind(k) for k in served),
        )

    def kind_choices(self) -> list[str]:
        """The kinds as the model is shown them: the table's names first, then other served resources."""
        table = [kind for kind in KIND_TABLE if kind in self._canonical]
        named = {a for kind in table for a in KIND_TABLE[kind]}
        return [*table, *sorted(k for k in self.kinds if k not in named)]

    def has_kind(self, kind: str) -> bool:
        return not self.kinds or canonical_kind(kind) in self._canonical or kind.lower() in self.kinds


def known_for(store: Store, environment: Environment) -> Known:
    """The served kinds from the last connection, and the namespaces of the latest whole inventory."""
    found = store.find(Inventory, {"environment_id": environment.id}, order_by="taken_at DESC", limit=20)
    whole = next((i for i in found if not i.namespaces), None)
    namespaces: set[str] = set()
    if whole is not None:
        namespaces = {i.ref.namespace for i in whole.items if i.ref.namespace}
    return Known.of(environment.kinds, namespaces, whole.taken_at if whole else None)


def _known_fields(model: type[BaseModel]) -> dict[str, str]:
    return cast(dict[str, str], getattr(model, "known_fields", {}))


def canonical(model: type[BaseModel], raw: dict[str, Any]) -> dict[str, Any]:
    """`raw` with declared aliases renamed and known kinds in their canonical form; unknown fields stay, so
    validation still refuses them (guarantee 2)."""
    fields = model.model_fields
    out: dict[str, Any] = {}
    for key, value in raw.items():
        target = FIELD_ALIASES.get(key, key)
        if target != key and (target not in fields or target in raw):
            target = key  # not an alias for this model, or the canonical name was sent as well
        out[target] = value
    for name, what in _known_fields(model).items():
        value = out.get(name)
        if what == "kinds" and isinstance(value, str) and value.partition(".")[0].lower() in _TABLE_NAMES:
            out[name] = canonical_kind(value)
    return out


_TABLE_NAMES = frozenset(a for aliases in KIND_TABLE.values() for a in aliases) | {
    k.lower() for k in KIND_TABLE
}


def unknown_values(model: type[BaseModel], inputs: BaseModel, known: Known) -> list[tuple[str, str]]:
    """Problems with values that are not in the environment: a kind it does not serve, a namespace the
    latest inventory does not have."""
    problems: list[tuple[str, str]] = []
    for name, what in _known_fields(model).items():
        value = getattr(inputs, name, None)
        values = cast(list[Any], value) if isinstance(value, list) else [value]
        for v in values:
            problem = _unknown(v, what, known) if isinstance(v, str) else None
            if problem:
                problems.append((name, problem))
    return problems


def _unknown(value: str, what: str, known: Known) -> str | None:
    if what == "kinds" and not known.has_kind(value):
        return f"{value!r} is not a kind this cluster serves{_suggest(value, known.kind_choices())}"
    if what == "namespaces" and known.namespaces and value not in known.namespaces:
        when = f" of {known.inventory_at:%Y-%m-%d %H:%M} UTC" if known.inventory_at else ""
        suggestion = _suggest(value, sorted(known.namespaces))
        return (
            f"{value!r} is not a namespace in this environment{suggestion} "
            f"(from the inventory{when}; if it was created since, take an inventory)"
        )
    return None


def _suggest(value: str, choices: list[str]) -> str:
    close = difflib.get_close_matches(value, choices, n=2, cutoff=0.6)
    if not close:  # `label_sel` -> `selector`: a word of the value that starts a choice
        words = [w.lower() for w in re.split(r"[_.\-]|(?<=[a-z])(?=[A-Z])", value) if len(w) >= 3]
        close = [c for c in choices if any(c.lower().startswith(w) for w in words)][:2]
    return f"; did you mean {' or '.join(repr(c) for c in close)}?" if close else ""


def explain(capability_id: str, model: type[BaseModel], problems: list[tuple[str, str]]) -> str:
    """The invalid call's message: each problem with a suggestion, then the accepted fields."""
    fields = list(model.model_fields)
    lines = [f"{capability_id}: invalid inputs"]
    for name, message in problems:
        hint = ""
        if message.startswith("Extra inputs"):
            alias = FIELD_ALIASES.get(name)
            hint = f"; did you mean {alias!r}?" if alias in fields else _suggest(name, fields)
        lines.append(f"- {name}: {message}{hint}")
    lines.append("accepted fields: " + ", ".join(_describe(model, f) for f in fields))
    return "\n".join(lines)


def _describe(model: type[BaseModel], name: str) -> str:
    info = model.model_fields[name]
    return f"{name} (required)" if info.is_required() else name


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
