"""Config and instance-pool loading helpers for CLI commands."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.tournament.application.entrypoints import build_tournament_run_config
from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import TournamentRuntimePort
from shogiarena._core.interfaces.cli.config_file_loaders import parse_tournament_config_file
from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.exceptions import ContractParseError

LOGGER = logging.getLogger("shogiarena.cli.instances")


def load_tournament_run_config(
    config_file: str | Path,
    *,
    runtime: TournamentRuntimePort | None = None,
) -> Any:
    """Load tournament-like config via interface boundary parser."""

    if runtime is None:
        runtime = build_default_root().tournament_runtime
    config_path = Path(config_file).resolve()
    try:
        parsed_payload = parse_tournament_config_file(config_path)
        return build_tournament_run_config(
            parsed_payload,
            base_dir=config_path.parent,
            source_path=config_path,
            runtime=runtime,
        )
    except (ContractParseError, TypeError, ValueError, OSError) as exc:
        raise CliError(f"Invalid tournament config: {config_path}: {exc}") from exc


def load_tournament_run_config_payload(
    payload: Mapping[str, object],
    *,
    base_dir: Path,
    source_path: Path | None = None,
    runtime: TournamentRuntimePort | None = None,
) -> Any:
    """Build tournament-like config from an already-materialized CLI payload."""

    if runtime is None:
        runtime = build_default_root().tournament_runtime
    try:
        return build_tournament_run_config(
            payload,
            base_dir=base_dir,
            source_path=source_path,
            runtime=runtime,
        )
    except (ContractParseError, TypeError, ValueError, OSError) as exc:
        raise CliError(f"Invalid tournament config payload: {exc}") from exc


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


__all__ = ["load_instance_pool_from_sources", "load_tournament_run_config", "load_tournament_run_config_payload"]
