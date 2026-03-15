"""Remote execution control extracted from BaseOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rshogi.core import normalize_usi_position

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import detect_git_remote_and_ref
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import remote_project_root
from shogiarena._core.platform.engine_provisioning.remote_repo_manager import RemoteRepoSpec
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort
from shogiarena._core.shared.kernel.time_control import TimeControlLimits
from shogiarena._core.shared.kernel.time_control_resolution import time_control_limits_to_dict


def get_remote_executor(
    orchestrator: Any,
    remote_instance: Instance,
    *,
    artifact_resolver: ArtifactResolutionPort | None = None,
) -> RemoteExecutor:
    """Return a cached RemoteExecutor for the provided instance."""

    owner = orchestrator
    name = remote_instance.name
    if not name:
        raise ValueError("remote_instance must provide a non-empty name")
    existing = owner._remote_executors.get(name)
    if existing is not None:
        return existing
    repo_url, repo_ref = detect_git_remote_and_ref()
    remote_root = remote_project_root(remote_instance)
    executor = RemoteExecutor(
        remote_instance,
        RemoteRepoSpec(base=remote_root, url=repo_url, ref=repo_ref),
        artifact_resolver=artifact_resolver,
    )
    owner._remote_executors[name] = executor
    return executor


async def ensure_remote_repo(
    orchestrator: Any,
    executor: RemoteExecutor,
    remote_instance: Instance,
    remote_root: str | None = None,
) -> str:
    """Ensure the remote repository is cloned/updated once per instance and return its root."""

    owner = orchestrator
    name = remote_instance.name
    if not name:
        raise ValueError("remote_instance must provide a non-empty name")
    resolved_root = remote_root or remote_project_root(remote_instance)
    signature = executor.cache_signature()
    key = f"{name}::{resolved_root}::{signature}"
    if key not in owner._remote_repo_ready:
        await executor.ensure_repo(resolved_root)
        owner._remote_repo_ready.add(key)
    return resolved_root


async def _resolve_remote_binaries(executor: RemoteExecutor, *config_paths: Path) -> list[str]:
    """Resolve local binaries and provision them to the remote host."""

    remote_paths: list[str] = []
    for config_path in config_paths:
        local_binary = await executor.resolve_local_binary(config_path)
        remote_binary = await executor.provision_engine_binary(local_binary)
        remote_paths.append(remote_binary)
    return remote_paths


async def _rewrite_remote_options(
    remote_instance: Instance,
    *option_sets: JsonObject | None,
    engine_factory_service: EngineFactoryService,
) -> None:
    """Rewrite option dictionaries so paths are valid for the remote environment."""

    for opts in option_sets:
        if not opts:
            continue
        await engine_factory_service.rewrite_options_for_remote(remote_instance, opts)


def _build_remote_run_spec(
    *,
    game_id: str,
    start_sfen: str,
    black_name: str,
    white_name: str,
    black_engine_binary_path: str,
    white_engine_binary_path: str,
    black_options: JsonObject,
    white_options: JsonObject,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    max_plies: int = 0,
) -> JsonObject:
    """Build the JSON spec consumed by remote pair runners."""

    return {
        "game_id": game_id,
        "initial_sfen": normalize_usi_position(start_sfen),
        "max_plies": max(0, int(max_plies)),
        "black": {
            "name": black_name,
            "engine_path": black_engine_binary_path,
            "options": black_options,
        },
        "white": {
            "name": white_name,
            "engine_path": white_engine_binary_path,
            "options": white_options,
        },
        "time_control": {
            "black": time_control_limits_to_dict(black_limits),
            "white": time_control_limits_to_dict(white_limits),
        },
    }


async def prepare_remote_game_spec(
    orchestrator: Any,
    *,
    remote_instance: Instance,
    black_config_path: Path,
    white_config_path: Path,
    black_options: JsonObject | None,
    white_options: JsonObject | None,
    start_sfen: str,
    game_id: str,
    black_name: str,
    white_name: str,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    max_plies: int,
    engine_factory_service: EngineFactoryService,
) -> tuple[RemoteExecutor, str, JsonObject]:
    """Prepare executor, remote root, and run spec for remote pair execution."""

    executor = get_remote_executor(
        orchestrator,
        remote_instance,
        artifact_resolver=engine_factory_service.artifact_resolver,
    )
    remote_root = await ensure_remote_repo(orchestrator, executor, remote_instance)
    black_remote_bin, white_remote_bin = await _resolve_remote_binaries(executor, black_config_path, white_config_path)
    black_opts = dict(black_options or {})
    white_opts = dict(white_options or {})
    await _rewrite_remote_options(
        remote_instance, black_opts, white_opts, engine_factory_service=engine_factory_service
    )
    spec = _build_remote_run_spec(
        game_id=game_id,
        start_sfen=start_sfen,
        black_name=black_name,
        white_name=white_name,
        black_engine_binary_path=black_remote_bin,
        white_engine_binary_path=white_remote_bin,
        black_options=black_opts,
        white_options=white_opts,
        black_limits=black_limits,
        white_limits=white_limits,
        max_plies=max_plies,
    )
    return executor, remote_root, spec


__all__ = [
    "ensure_remote_repo",
    "get_remote_executor",
    "prepare_remote_game_spec",
]
