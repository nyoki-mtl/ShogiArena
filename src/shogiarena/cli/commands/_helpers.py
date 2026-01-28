"""Shared helpers for CLI command implementations."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace as dataclass_replace
from pathlib import Path

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common.paths import resolve_path_like

LOGGER = logging.getLogger("shogiarena.cli.instances")


def parse_option_overrides(option_list: list[str] | None) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` option overrides."""

    if not option_list:
        return {}
    overrides: dict[str, str] = {}
    for raw in option_list:
        if "=" not in raw:
            raise CliArgumentError(f"invalid option override (expected KEY=VALUE): {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CliArgumentError("option key must not be empty")
        overrides[key] = value.strip()
    return overrides


def load_instance_pool(instances_path: str | None) -> InstancePool | None:
    """Load an instances.yaml file when provided."""

    if not instances_path:
        return None
    resolved = Path(resolve_path_like(instances_path))
    if not resolved.exists():
        raise CliArgumentError(f"instances file not found: {resolved}")
    return InstancePool.load_from_yaml(resolved)


def load_instance_pool_from_sources(sources: Sequence[Path]) -> InstancePool | None:
    """Load and merge instances from multiple YAML files or directories."""

    if not sources:
        return None

    files: list[Path] = []
    seen_files: set[Path] = set()
    for source in sources:
        if not source.exists():
            raise CliError(f"instances source not found: {source}")
        if source.is_dir():
            candidates = sorted(source.glob("*.yaml")) + sorted(source.glob("*.yml"))
            if not candidates:
                LOGGER.warning("No instance YAML files found in directory: %s", source)
            for candidate in candidates:
                resolved_path = candidate.resolve()
                if resolved_path in seen_files:
                    continue
                seen_files.add(resolved_path)
                files.append(resolved_path)
        else:
            resolved_file = source.resolve()
            if resolved_file in seen_files:
                raise CliError(f"Duplicate instances source specified: {resolved_file}")
            seen_files.add(resolved_file)
            files.append(resolved_file)

    if not files:
        return None

    merged_pool = InstancePool()
    for file_path in files:
        sub_pool = InstancePool.load_from_yaml(file_path)
        for instance in sub_pool.list_instances():
            config_copy = dataclass_replace(instance.config)
            try:
                merged_pool.add_instance(config_copy, source_path=instance.source_path or file_path)
            except ValueError as exc:
                raise CliError(
                    f"Duplicate instance name '{instance.name}' encountered when loading {file_path}"
                ) from exc
    return merged_pool if merged_pool.list_instances() else None


def merge_options(
    base: Mapping[str, str],
    overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    """Return a new dict merging overrides onto base."""

    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged
