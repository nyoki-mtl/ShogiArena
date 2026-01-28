"""Remote execution utilities for orchestrators."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.remote.executor import RemoteExecutor
from shogiarena.arena.remote.repo_manager import RemoteRepoSpec
from shogiarena.utils.board import normalize_sfen

from . import base_orchestrator_utils


class RemoteExecutionManager:
    """Manage remote execution helpers for orchestrators."""

    def __init__(self) -> None:
        self._remote_repo_ready: set[str] = set()
        self._remote_executors: dict[str, RemoteExecutor] = {}

    def get_remote_executor(self, remote_instance: Any) -> RemoteExecutor:
        name = str(getattr(remote_instance, "name", ""))
        if not name:
            raise ValueError("remote_instance must provide a non-empty name")
        existing = self._remote_executors.get(name)
        if existing is not None:
            return existing
        repo_url, repo_ref = base_orchestrator_utils.detect_git_remote_and_ref()
        remote_root = base_orchestrator_utils.remote_project_root(remote_instance)
        executor = RemoteExecutor(
            remote_instance,
            RemoteRepoSpec(base=remote_root, url=repo_url, ref=repo_ref),
        )
        self._remote_executors[name] = executor
        return executor

    async def ensure_remote_repo(
        self,
        executor: RemoteExecutor,
        remote_instance: Any,
        remote_root: str | None = None,
    ) -> str:
        name = str(getattr(remote_instance, "name", ""))
        if not name:
            raise ValueError("remote_instance must provide a non-empty name")
        resolved_root = remote_root or base_orchestrator_utils.remote_project_root(remote_instance)
        signature = executor.cache_signature()
        key = f"{name}::{resolved_root}::{signature}"
        if key not in self._remote_repo_ready:
            await executor.ensure_repo(resolved_root)
            self._remote_repo_ready.add(key)
        return resolved_root

    async def resolve_remote_binaries(self, executor: RemoteExecutor, *config_paths: Path) -> list[str]:
        remote_paths: list[str] = []
        for config_path in config_paths:
            local_binary = await executor.resolve_local_binary(config_path)
            remote_binary = await executor.provision_engine_binary(local_binary)
            remote_paths.append(remote_binary)
        return remote_paths

    async def rewrite_remote_options(self, remote_instance: Any, *option_sets: dict[str, Any] | None) -> None:
        for opts in option_sets:
            if not opts:
                continue
            await EngineFactory._rewrite_options_for_remote(remote_instance, opts)

    @staticmethod
    def build_remote_run_spec(
        *,
        game_id: str,
        start_sfen: str,
        black_name: str,
        white_name: str,
        black_engine_binary_path: str,
        white_engine_binary_path: str,
        black_options: dict[str, Any],
        white_options: dict[str, Any],
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        max_plies: int = 0,
    ) -> dict[str, Any]:
        return {
            "game_id": game_id,
            "initial_sfen": normalize_sfen(start_sfen),
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
                "black": base_orchestrator_utils.time_control_limits_to_dict(black_limits),
                "white": base_orchestrator_utils.time_control_limits_to_dict(white_limits),
            },
        }

    async def prepare_remote_game_spec(
        self,
        *,
        remote_instance: Any,
        black_config_path: Path,
        white_config_path: Path,
        black_options: dict[str, Any] | None,
        white_options: dict[str, Any] | None,
        start_sfen: str,
        game_id: str,
        black_name: str,
        white_name: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        max_plies: int,
    ) -> tuple[RemoteExecutor, str, dict[str, Any]]:
        executor = self.get_remote_executor(remote_instance)
        remote_root = await self.ensure_remote_repo(executor, remote_instance)

        black_remote_bin, white_remote_bin = await self.resolve_remote_binaries(
            executor, black_config_path, white_config_path
        )

        black_opts = dict(black_options or {})
        white_opts = dict(white_options or {})
        await self.rewrite_remote_options(remote_instance, black_opts, white_opts)

        spec = self.build_remote_run_spec(
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
