"""The registry with every built-in capability and the loaded extensions (design §5.3, §5.5)."""

from __future__ import annotations

from pathlib import Path

from k8sholmes.capabilities import Registry, kubernetes, probes, reviews
from k8sholmes.capabilities import knowledge as knowledge_caps
from k8sholmes.capabilities.extensions import load_command_capabilities, load_entry_points


def builtin_registry(
    *, home: Path | None = None, allowed_executables: list[str] | None = None, extensions: bool = True
) -> Registry:
    registry = Registry(
        [*kubernetes.CAPABILITIES, *reviews.CAPABILITIES, *knowledge_caps.CAPABILITIES, *probes.CAPABILITIES]
    )
    if extensions:
        load_entry_points(registry)
        if home is not None:
            load_command_capabilities(
                registry, home / "capabilities", allowed_executables=allowed_executables or []
            )
    return registry
