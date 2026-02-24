"""Implementation of the ``shogiarena run spsa`` command."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from shogiarena.arena.configs.spsa import SpsaRunConfig, load_config_yaml
from shogiarena.arena.runners.spsa_runner import SpsaRunner
from shogiarena.arena.services.artifacts.builder import maybe_build_from_yaml
from shogiarena.arena.storage import FilesystemRunStorage
from shogiarena.cli.errors import CliArgumentError
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.settings import SETTINGS, validate_overlays

from .base_run import BaseRunCommand

LOGGER = logging.getLogger("shogiarena.cli.run_spsa")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "run-spsa",
        help="Run an SPSA tuning session from a YAML configuration",
    )
    parser.add_argument("config", help="Path to SPSA configuration YAML")
    parser.add_argument("--engine-trace", action="store_true", help="Enable verbose USI engine logging")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without executing")
    parser.add_argument("--validate-only", action="store_true", help="Validate config and exit without scheduling")
    parser.add_argument("--no-resume", action="store_true", help="Start a new run instead of resuming")
    parser.add_argument(
        "--experiment-name",
        help="Override the experiment name (creates runs/<name>-<hash8>/timestamp)",
    )
    parser.add_argument(
        "--run-dir",
        help="Override the run directory (absolute or relative path)",
    )
    parser.add_argument(
        "--provision",
        choices=["none", "force"],
        default="none",
        help="Provision engine directories to SSH instances before run",
    )
    parser.add_argument(
        "--git-worktree",
        choices=["strict", "clean", "allow-dirty"],
        default="strict",
        help="Control git worktree handling before builds",
    )
    parser.set_defaults(handler=lambda args: SpsaRunCommand(args).run())


class SpsaRunCommand(BaseRunCommand):
    def run(self) -> None:
        args = self.args
        config_path = self.resolve_config_path(args.config)

        if args.experiment_name and args.run_dir:
            raise CliArgumentError("--experiment-name and --run-dir cannot be used together")

        run_spsa_sync(
            config_file=config_path,
            engine_trace=args.engine_trace,
            dry_run=args.dry_run,
            validate_only=args.validate_only,
            no_resume=args.no_resume,
            provision_mode=args.provision,
            git_worktree=args.git_worktree,
            experiment_name=args.experiment_name,
            run_dir_override=args.run_dir,
            base_cmd=self,
        )


def run_spsa_sync(
    *,
    config_file: Path,
    engine_trace: bool,
    dry_run: bool,
    validate_only: bool = False,
    no_resume: bool,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    logger = LOGGER
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())

    cfg: SpsaRunConfig = load_config_yaml(config_file)
    validate_overlays(SETTINGS)

    if validate_only:
        logger.info("Validated SPSA config: %s", config_file)
        return

    run_dir = cmd.resolve_run_dir_path(
        config_file,
        project_dirs.output_dir / "spsa",
        experiment_name,
        run_dir_override,
    )

    resume_dir = cmd.prompt_resume(
        config_file=config_file,
        output_dir=project_dirs.output_dir / "spsa",
        experiment_name=experiment_name,
        run_dir_override=run_dir_override,
        no_resume=no_resume,
        dry_run=dry_run,
        scan_candidates_fn=_scan_spsa_candidates,
        match_hash=None,  # SPSA config hashing not strictly enforced yet
    )
    if resume_dir is not None:
        run_dir = resume_dir

    instance_pool = cmd.resolve_instance_pool(cfg.instances)

    # SPSA specific: combine baseline and tuned for common ops
    all_engines = cfg.baseline + cfg.tuned

    cmd.apply_git_worktree(all_engines, git_worktree)

    if engine_trace or logging.getLogger().level == logging.DEBUG:
        for name in [
            "shogiarena.arena.engines.usi_engine",
            "shogiarena.arena.execution.game_runner",
            "shogiarena.arena.orchestrators.spsa_orchestrator",
        ]:
            logging.getLogger(name).setLevel(logging.DEBUG)
        logger.info("[SPSA] Engine trace enabled: logging all USI commands and outputs")

    if not dry_run:
        maybe_build_from_yaml(config_file)

    if instance_pool and provision_mode == "force" and not dry_run:
        cmd.provision_engines(all_engines, instance_pool)

    if dry_run:
        logger.info("DRY RUN MODE: Validated SPSA config")
        base_names = [e.name for e in cfg.baseline]
        tuned_names = [e.name for e in cfg.tuned]
        logger.debug("Experiment: %s", cfg.experiment_name)
        logger.debug("Run directory: %s", run_dir)
        logger.debug("Baseline: %s", base_names)
        logger.debug("Tuned: %s", tuned_names)
        logger.debug("Parameters path: %s", cfg.parameters_path)
        logger.debug("Start SFENs: %s", cfg.start_sfens_path)
        return

    storage = FilesystemRunStorage(run_dir)
    runner = SpsaRunner(cfg, storage=storage, no_resume=no_resume, instance_pool=instance_pool)
    runner.run_sync()


def _scan_spsa_candidates(group_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in sorted(group_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        if not (entry.name.isdigit() and len(entry.name) == 14):
            continue

        state_path = entry / "run_state.json"

        if not state_path.exists():
            continue

        updated_at = "-"
        finished = False
        completed = 0
        total = 0

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if isinstance(state, dict):
            updated_at = state.get("updated_at") or state.get("created_at") or updated_at
            completed = int(state.get("completed_updates", 0) or 0)
            total = int(state.get("total_updates", 0) or 0)
            finished = bool(state.get("finished", False))

        candidates.append(
            {
                "path": entry,
                "slug": entry.name,
                "completed": completed,
                "total": total,
                "updated_at": updated_at,
                "finished": finished,
                "schedule_hash": None,  # SPSA runs don't typically hash the schedule in the same way
            }
        )
    return candidates
