from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.instances.provision import Provisioner, ProvisionError
from shogiarena.cli.commands._helpers import load_instance_pool_from_sources
from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.run_paths import run_dir_for_name, run_group_dir, run_group_dir_for_name

LOGGER = logging.getLogger(__name__)


class BaseRunCommand:
    """Base class for run commands (tournament, spsa, etc.) with common CLI behavior."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.logger = LOGGER

    def run(self) -> None:
        """Execute the command."""
        raise NotImplementedError("Subclasses must implement run()")

    def resolve_config_path(self, config_arg: str | None) -> Path:
        if not config_arg:
            raise CliError("configuration file is required")
        path = Path(resolve_path_like(config_arg))
        if not path.exists():
            raise CliError(f"configuration file not found: {path}")
        return path

    def resolve_instance_pool(
        self,
        config_instances: Sequence[Path] | None,
    ) -> InstancePool | None:
        if config_instances:
            pool = load_instance_pool_from_sources(config_instances)
            if pool is not None:
                return pool
        return InstancePool.load_default_local()

    def apply_run_dir_override(
        self,
        config: Any,
        config_file: Path,
        output_dir: Path,
        run_name: str | None,
        run_dir_override: str | None,
    ) -> None:
        def _assign_run_dir(value: Path) -> None:
            if not hasattr(config, "run_dir"):
                return
            current = getattr(config, "run_dir", None)
            if isinstance(current, Path):
                config.run_dir = value
            else:
                config.run_dir = str(value)

        if run_dir_override:
            # Explicit path overrides everything
            path = Path(resolve_path_like(run_dir_override))
            _assign_run_dir(path)
            return

        # If run_name is provided, use standard naming scheme
        if run_name:
            try:
                path = run_dir_for_name(config_file, output_dir, run_name)
                _assign_run_dir(path)
            except ValueError as exc:
                raise CliArgumentError(str(exc)) from exc

    def prompt_resume(
        self,
        config: Any,
        config_file: Path,
        output_dir: Path,
        run_name: str | None,
        run_dir_override: str | None,
        overwrite: bool,
        dry_run: bool,
        scan_candidates_fn: Callable[[Path], list[dict[str, Any]]],
        match_hash: str | None = None,
    ) -> None:
        """Interactive prompt to resume previous runs if applicable."""
        if run_dir_override or overwrite or dry_run:
            return
        if not sys.stdin.isatty():
            return

        try:
            group_dir = (
                run_group_dir_for_name(config_file, output_dir, run_name)
                if run_name
                else run_group_dir(config_file, output_dir)
            )
        except ValueError:
            return
        if not group_dir.exists():
            return

        candidates = scan_candidates_fn(group_dir)
        if not candidates:
            return

        resumable: list[dict[str, Any]] = []
        print("Found previous runs for this config:")
        for item in candidates:
            status = "finished" if item["finished"] else "incomplete"
            mismatch = ""
            if match_hash and item.get("schedule_hash") and item["schedule_hash"] != match_hash:
                mismatch = " (config mismatch)"

            # SPSA specific check or generic check
            not_resumable = " (not resumable)" if item["finished"] or mismatch else ""
            print(
                f"  {item['slug']}  completed {item['completed']}/{item['total']}  "
                f"updated {item['updated_at']}  {status}{mismatch}{not_resumable}"
            )
            if not item["finished"] and not mismatch:
                resumable.append(item)

        if not resumable:
            self._confirm_new_run()
            return

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
                return
            if not choice.isdigit():
                print("Please enter a number, N, or Q.")
                continue
            idx = int(choice)
            selected = next((item for item in resumable if item["idx"] == idx), None)
            if selected is None:
                print(f"Select between 1 and {len(resumable)}.")
                continue

            # Apply selection
            if hasattr(config, "run_dir"):
                config.run_dir = selected["path"]
            return

    def _confirm_new_run(self) -> None:
        while True:
            choice = input("No resumable runs found. Start new? [N=new / Q=quit]: ").strip().lower()
            if choice in {"q", "quit"}:
                raise SystemExit(0)
            if choice in {"n", "new"}:
                return
            print("Please enter N or Q.")

    def apply_git_worktree(self, engine_specs: list[Any], mode: str) -> None:
        for engine in engine_specs:
            # Check for artifact or build_options usage
            # Adapting to different object structures (Arena vs SPSA)
            build_opts = dict(getattr(engine, "build_options", {}) or {})

            if mode == "clean":
                build_opts["git_clean"] = "force"
                build_opts.pop("allow_dirty_build", None)
            elif mode == "allow-dirty":
                build_opts.pop("git_clean", None)
                build_opts["allow_dirty_build"] = True
            else:
                build_opts.pop("git_clean", None)
                build_opts.pop("allow_dirty_build", None)

            if hasattr(engine, "build_options"):
                engine.build_options = build_opts

        if mode == "clean":
            self.logger.info("git-worktree=clean: forcing git reset --hard && git clean -fdx before builds")
        elif mode == "allow-dirty":
            self.logger.info(
                "git-worktree=allow-dirty: permitting dirty sources; binaries will be suffixed with '-dirty'"
            )

    def provision_engines(self, engine_specs: list[Any], instance_pool: InstancePool) -> None:
        async def _provision(engine_spec: Any) -> None:
            inst_id = getattr(engine_spec, "instance_id", None)
            if not inst_id:
                return
            instance = instance_pool.get_instance(inst_id)
            if instance is None or not instance.is_ssh:
                return

            # Determine local engine path
            # SPSA has engine_dir, Arena has engine_config -> engine_path
            local_path: Path | None = None

            # SPSA style
            if hasattr(engine_spec, "engine_dir") and engine_spec.engine_dir:
                local_path = Path(engine_spec.engine_dir)

            # Arena style
            elif hasattr(engine_spec, "engine_config"):
                cfg_path = engine_spec.engine_config
                if cfg_path and Path(cfg_path).exists():
                    with open(cfg_path, encoding="utf-8") as f:
                        raw = yaml.safe_load(f) or {}
                    ep = raw.get("engine_path")
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
                raise CliError(f"provision failed for {getattr(engine_spec, 'name', 'engine')}: {exc}") from exc

        for spec in engine_specs:
            asyncio.run(_provision(spec))
