from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.provisioner import Provisioner, ProvisionError
from shogiarena._core.interfaces.cli.config_file_loaders import parse_engine_config_file
from shogiarena._core.interfaces.cli.config_loader import load_instance_pool_from_sources
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.run_paths import (
    default_run_dir,
    run_dir_for_key,
    run_dir_for_name,
    run_group_dir,
    run_group_dir_for_key,
    run_group_dir_for_name,
)

LOGGER = logging.getLogger(__name__)


class ResumeCandidate(TypedDict, total=False):
    path: Path
    slug: str
    completed: int
    total: int
    updated_at: str
    is_finished: bool
    match_hash: str | None
    idx: int


class _EngineSpecPort(Protocol):
    build_options: Mapping[str, object]
    instance_id: str | None
    engine_path: Path | None
    name: str | None


@runtime_checkable
class _HashCacheOwnerPort(Protocol):
    def clear_run_artifact_hash_cache(self) -> None: ...


class BaseRunCommand:
    """Base class for run commands (tournament, spsa, etc.) with common CLI behavior."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.logger = LOGGER

    def run(self) -> None:
        """Execute the command."""
        raise NotImplementedError("Subclasses must implement run()")

    def resolve_instance_pool(
        self,
        config_instances: Sequence[Path] | None,
    ) -> InstancePool | None:
        if config_instances:
            pool = load_instance_pool_from_sources(config_instances)
            if pool is not None:
                return pool
        return InstancePool.load_default_local()

    def resolve_run_dir_path(
        self,
        config_file: Path,
        output_dir: Path,
        experiment_name: str | None,
        run_dir_override: str | None,
        schedule_hash: str | None = None,
    ) -> Path:
        if run_dir_override:
            return Path(resolve_path_like(run_dir_override))
        if schedule_hash:
            return run_dir_for_key(output_dir, experiment_name or config_file.stem, schedule_hash)
        if experiment_name:
            try:
                return run_dir_for_name(config_file, output_dir, experiment_name)
            except ValueError as exc:
                raise CliArgumentError(str(exc)) from exc
        return default_run_dir(config_file, output_dir)

    def prompt_resume(
        self,
        config_file: Path,
        output_dir: Path,
        experiment_name: str | None,
        run_dir_override: str | None,
        should_skip_resume: bool,
        is_dry_run: bool,
        scan_candidates_fn: Callable[[Path], list[ResumeCandidate]],
        match_hash: str | None = None,
        group_hash: str | None = None,
    ) -> Path | None:
        """Interactive prompt to resume previous runs if applicable."""
        if run_dir_override or should_skip_resume or is_dry_run:
            return None
        if not sys.stdin.isatty():
            return None

        try:
            if group_hash:
                group_dir = run_group_dir_for_key(output_dir, experiment_name or config_file.stem, group_hash)
            else:
                group_dir = (
                    run_group_dir_for_name(config_file, output_dir, experiment_name)
                    if experiment_name
                    else run_group_dir(config_file, output_dir)
                )
        except ValueError:
            return None
        if not group_dir.exists():
            return None

        candidates = scan_candidates_fn(group_dir)
        if not candidates:
            return None

        resumable: list[ResumeCandidate] = []
        print("Found previous runs for this config:")
        for item in candidates:
            status = "finished" if item["is_finished"] else "incomplete"
            mismatch = ""
            if match_hash and item.get("match_hash") != match_hash:
                mismatch = " (config mismatch)"

            # SPSA specific check or generic check
            not_resumable = " (not resumable)" if item["is_finished"] or mismatch else ""
            print(
                f"  {item['slug']}  completed {item['completed']}/{item['total']}  "
                f"updated {item['updated_at']}  {status}{mismatch}{not_resumable}"
            )
            if not item["is_finished"] and not mismatch:
                resumable.append(item)

        if not resumable:
            self._confirm_new_run()
            return None

        for idx, item in enumerate(resumable, start=1):
            item["idx"] = idx

        print("Resumable runs:")
        for item in resumable:
            print(
                f"  [{item['idx']}] {item['slug']}  completed {item['completed']}/{item['total']}  "
                f"updated {item['updated_at']}"
            )

        while True:
            choice = input("Resume which run? [number / N=new / Q=quit]: ").strip().lower()
            if choice in {"q", "quit"}:
                raise SystemExit(0)
            if choice in {"n", "new"}:
                return None
            if not choice.isdigit():
                print("Please enter a number, N, or Q.")
                continue
            idx = int(choice)
            selected = next((item for item in resumable if item["idx"] == idx), None)
            if selected is None:
                print(f"Select between 1 and {len(resumable)}.")
                continue

            # Apply selection
            return selected["path"]

    def _confirm_new_run(self) -> None:
        while True:
            choice = input("No resumable runs found. Start new? [N=new / Q=quit]: ").strip().lower()
            if choice in {"q", "quit"}:
                raise SystemExit(0)
            if choice in {"n", "new"}:
                return
            print("Please enter N or Q.")

    def apply_git_worktree(self, engine_specs: Sequence[_EngineSpecPort], mode: str) -> None:
        for engine in engine_specs:
            build_opts = dict(engine.build_options)

            if mode == "clean":
                build_opts["git_clean"] = "force"
                build_opts.pop("allow_dirty_build", None)
            elif mode == "allow-dirty":
                build_opts.pop("git_clean", None)
                build_opts["allow_dirty_build"] = True
            else:
                build_opts.pop("git_clean", None)
                build_opts.pop("allow_dirty_build", None)

            engine.build_options = build_opts

        if mode == "clean":
            self.logger.info("git-worktree=clean: forcing git reset --hard && git clean -fdx before builds")
        elif mode == "allow-dirty":
            self.logger.info(
                "git-worktree=allow-dirty: permitting dirty sources; binaries will be suffixed with '-dirty'"
            )

    @staticmethod
    def clear_hash_cache(config: object) -> None:
        if isinstance(config, _HashCacheOwnerPort):
            config.clear_run_artifact_hash_cache()

    async def provision_engines(self, engine_specs: Sequence[_EngineSpecPort], instance_pool: InstancePool) -> None:
        async def _provision(engine_spec: _EngineSpecPort) -> None:
            inst_id_obj = engine_spec.instance_id
            if not isinstance(inst_id_obj, str) or not inst_id_obj.strip():
                return
            inst_id = inst_id_obj.strip()
            instance = instance_pool.get_instance(inst_id)
            if instance is None or not instance.is_ssh:
                return

            # Determine local engine path from engine config YAML.
            local_path: Path | None = None
            cfg_path = engine_spec.engine_path
            if cfg_path is not None and cfg_path.exists():
                try:
                    engine_payload = parse_engine_config_file(cfg_path)
                except (ContractParseError, OSError, TypeError, ValueError) as exc:
                    engine_name = engine_spec.name or "engine"
                    raise CliError(f"invalid engine config for {engine_name}: {cfg_path}: {exc}") from exc
                ep = engine_payload.get("engine_path")
                if isinstance(ep, str) and ep.strip():
                    local_path = Path(resolve_path_like(ep)).parent

            if local_path is None or not local_path.exists():
                # fallback or skip
                return

            remote_dir = Path(instance.config.engine_dir) / local_path.name
            self.logger.info("[provision] %s -> %s:%s", local_path, instance.name, remote_dir)
            try:
                await Provisioner.copy_directory_scp(instance, local_path, str(remote_dir))
            except (ProvisionError, OSError) as exc:
                engine_name = engine_spec.name or "engine"
                raise CliError(f"provision failed for {engine_name}: {exc}") from exc

        for spec in engine_specs:
            await _provision(spec)
