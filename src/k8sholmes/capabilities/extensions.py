"""Extensions (design §5.5): Python entry points and command capabilities declared in TOML.

A command capability runs an executable from the admin's allow-list with an argument template whose
placeholders are the validated inputs. Templates never pass through a shell. An extension that fails to
load is listed as broken; nothing else is affected.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import subprocess
import tomllib
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from k8sholmes.capabilities import Capability, Context, Registry
from k8sholmes.evidence import Result
from k8sholmes.records import CapabilityClass, Coverage

ENTRY_POINT_GROUP = "k8sholmes.capabilities"
TYPES: dict[str, type] = {"string": str, "integer": int, "number": float, "boolean": bool}


def load_entry_points(registry: Registry) -> None:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            loaded = ep.load()
            caps = loaded() if callable(loaded) else loaded
            for cap in cast(Iterable[Capability], caps):
                registry.add(dataclasses.replace(cap, origin=f"entry point {ep.name}"))
        except Exception as exc:  # a broken extension is reported, never fatal
            registry.broken[f"entry point {ep.name}"] = f"{type(exc).__name__}: {exc}"


def _input_model(name: str, fields: dict[str, Any]) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    for field_name, spec in fields.items():
        spec = cast(dict[str, Any], spec)
        python_type = TYPES.get(str(spec.get("type", "string")), str)
        description = str(spec.get("description", ""))
        if "default" in spec:
            definitions[field_name] = (python_type, Field(default=spec["default"], description=description))
        else:
            definitions[field_name] = (python_type, Field(description=description))
    return create_model(name, __config__=ConfigDict(extra="forbid"), **definitions)


def command_capability(
    spec: dict[str, Any], *, allowed_executables: Iterable[str], origin: str
) -> Capability:
    """A capability from one TOML table (see docs/extensions.md)."""
    executable = str(spec["executable"])
    if executable not in set(allowed_executables):
        raise ValueError(f"executable {executable!r} is not in the allow-list")
    template = [str(a) for a in cast(list[Any], spec.get("args", []))]
    timeout_ms = int(spec.get("timeout_ms", 20_000))
    inputs = _input_model(str(spec["id"]).replace(".", "_"), cast(dict[str, Any], spec.get("inputs", {})))
    class_ = cast(CapabilityClass, spec.get("class", "inspect"))
    if class_ not in ("inspect", "probe", "change"):
        raise ValueError(f"class {class_!r} is not inspect, probe or change")
    coverage_text = str(spec.get("coverage", "observed"))
    if coverage_text not in ("observed", "observed_absent", "no_data", "cannot_answer"):
        raise ValueError(f"coverage {coverage_text!r} is not one of the four statements")
    coverage: Coverage = coverage_text

    def run(values: BaseModel, ctx: Context) -> Result:
        fields = values.model_dump()
        args = [a.format(**fields) for a in template]
        done = subprocess.run(  # noqa: S603
            [executable, *args], capture_output=True, text=True, timeout=timeout_ms / 1000, check=False
        )
        output: Any = done.stdout
        with contextlib.suppress(ValueError):
            output = json.loads(done.stdout)
        if done.returncode != 0:
            return Result(
                summary=f"{executable} exited {done.returncode}",
                data={"stdout": output, "stderr": done.stderr[-2000:]},
                coverage="no_data",
                coverage_reason="the command failed",
                error=done.stderr.strip()[-500:] or f"exit {done.returncode}",
            )
        return Result(
            summary=f"{executable} {' '.join(args)}"[:200],
            data=output,
            coverage=coverage,
            coverage_reason="a command capability",
        )  # type: ignore[arg-type]

    return Capability(
        id=str(spec["id"]),
        title=str(spec.get("title", spec["id"])),
        purpose=str(spec.get("purpose", "")),
        class_=class_,
        systems=tuple(str(s) for s in cast(list[Any], spec.get("systems", ["command"]))),
        inputs=inputs,
        permissions=(),
        run=run,
        origin=origin,
        coverage=(coverage,),
    )


def load_command_capabilities(
    registry: Registry, folder: Path, *, allowed_executables: Iterable[str]
) -> None:
    if not folder.is_dir():
        return
    allowed = list(allowed_executables)
    for path in sorted(folder.glob("*.toml")):
        origin = f"command {path.name}"
        try:
            document = tomllib.loads(path.read_text())
            for spec in cast(list[dict[str, Any]], document.get("capability", [])):
                registry.add(command_capability(spec, allowed_executables=allowed, origin=origin))
        except Exception as exc:  # a broken file is reported, never fatal
            registry.broken[origin] = f"{type(exc).__name__}: {exc}"
