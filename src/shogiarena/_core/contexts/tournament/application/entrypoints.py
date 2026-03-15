"""Tournament application entrypoints exposed to interfaces."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import (
    TournamentRunConfigBuildRequest,
    TournamentRuntimePort,
)


def build_tournament_run_config(
    payload: Mapping[str, object],
    *,
    base_dir: Path,
    source_path: Path | None,
    runtime: TournamentRuntimePort,
) -> Any:
    """Build TournamentRunConfig from boundary payload."""

    request = TournamentRunConfigBuildRequest(base_dir=base_dir, source_path=source_path)
    return runtime.build_run_config(payload, request=request)


async def run_tournament_session(
    config: Any,
    *,
    storage: Any,
    should_skip_resume: bool,
    instance_pool: object | None,
    runtime: TournamentRuntimePort,
) -> None:
    """Run a tournament-like session through the configured runner."""

    await runtime.run_session(
        config,
        storage=storage,
        should_skip_resume=should_skip_resume,
        instance_pool=instance_pool,
    )


def create_tournament_run_storage(run_dir: Path, *, runtime: TournamentRuntimePort) -> Any:
    """Create filesystem run storage for tournament-like sessions."""

    return runtime.create_run_storage(run_dir)


__all__ = [
    "build_tournament_run_config",
    "create_tournament_run_storage",
    "run_tournament_session",
]
