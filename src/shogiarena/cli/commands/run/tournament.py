"""Implementation of the ``shogiarena run tournament`` command."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.scheduler.game_scheduler import create_scheduler
from shogiarena.arena.services.artifacts.builder import maybe_build_from_yaml
from shogiarena.arena.storage import FilesystemRunStorage
from shogiarena.utils.common.settings import SETTINGS, validate_overlays

from .base_run import BaseRunCommand

LOGGER = logging.getLogger("shogiarena.cli.run_tournament")


def run_tournament_sync(
    *,
    config_file: Path,
    no_resume: bool,
    dry_run: bool,
    validate_only: bool = False,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    # Helper to allow standalone usage if needed (though discouraged now)
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())

    config = TournamentRunConfig.from_yaml(config_file)
    validate_overlays(SETTINGS)

    if validate_only:
        LOGGER.info("Validated tournament config: %s", config_file)
        return

    run_dir = cmd.resolve_run_dir_path(
        config_file,
        config.output_dir / "tournament",
        experiment_name,
        run_dir_override,
    )

    resume_dir = cmd.prompt_resume(
        config_file=config_file,
        output_dir=config.output_dir / "tournament",
        experiment_name=experiment_name,
        run_dir_override=run_dir_override,
        no_resume=no_resume,
        dry_run=dry_run,
        scan_candidates_fn=_scan_tournament_candidates,
        match_hash=config.get_schedule_hash(),
    )
    if resume_dir is not None:
        run_dir = resume_dir

    instance_pool = cmd.resolve_instance_pool(config.instances)

    cmd.apply_git_worktree(config.engines, git_worktree)

    if not dry_run:
        maybe_build_from_yaml(config_file)

    if instance_pool and provision_mode == "force" and not dry_run:
        cmd.provision_engines(config.engines, instance_pool)

    storage = FilesystemRunStorage(run_dir)
    runner = TournamentRunner(
        config,
        storage=storage,
        no_resume=no_resume,
        instance_pool=instance_pool,
    )

    if dry_run:
        _dry_run_schedule(config)
        return

    runner.run_sync()


def run_generate_sync(
    *,
    config_file: Path,
    no_resume: bool,
    dry_run: bool,
    validate_only: bool = False,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())

    config = TournamentRunConfig.from_yaml(config_file)
    validate_overlays(SETTINGS)

    if validate_only:
        LOGGER.info("Validated generate config: %s", config_file)
        return

    run_dir = cmd.resolve_run_dir_path(
        config_file,
        config.output_dir / "generate",
        experiment_name,
        run_dir_override,
    )

    if not no_resume:
        LOGGER.warning("Generate runs do not support resume; starting a new run")
        no_resume = True

    instance_pool = cmd.resolve_instance_pool(config.instances)

    cmd.apply_git_worktree(config.engines, git_worktree)

    if not dry_run:
        maybe_build_from_yaml(config_file)

    if instance_pool and provision_mode == "force" and not dry_run:
        cmd.provision_engines(config.engines, instance_pool)

    storage = FilesystemRunStorage(run_dir)
    runner = TournamentRunner(
        config,
        storage=storage,
        no_resume=no_resume,
        instance_pool=instance_pool,
    )

    if dry_run:
        _dry_run_schedule(config)
        return

    runner.run_sync()


def _scan_tournament_candidates(group_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in sorted(group_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        if not (entry.name.isdigit() and len(entry.name) == 14):
            continue
        state_path = entry / "run_state.json"
        if not state_path.exists():
            continue
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        completed = raw.get("completed_game_ids") or []
        total = raw.get("original_total_games") or raw.get("total_games") or 0
        updated_at = raw.get("updated_at") or raw.get("created_at") or "-"
        finished = bool(raw.get("finished"))
        schedule_hash = raw.get("schedule_hash")
        candidates.append(
            {
                "path": entry,
                "slug": entry.name,
                "completed": len(completed) if isinstance(completed, list) else 0,
                "total": int(total) if isinstance(total, int) else 0,
                "updated_at": updated_at,
                "finished": finished,
                "schedule_hash": schedule_hash,
            }
        )
    return candidates


def _scan_generate_candidates(group_dir: Path) -> list[dict[str, Any]]:
    return _scan_tournament_candidates(group_dir)


def _dry_run_schedule(config: TournamentRunConfig) -> None:
    logger = LOGGER
    logger.info("DRY RUN: generating tournament schedule")
    scheduler = create_scheduler(config.tournament.scheduler)
    schedule = scheduler.generate_schedule(
        engines=config.engines,
        games_per_pair=config.tournament.games_per_pair,
        seed=config.tournament.seed,
        initial_positions=config.rules.initial_positions,
    )
    logger.info("Generated schedule with %d games", len(schedule))
    for idx, game in enumerate(schedule[:10]):
        logger.debug(
            "  %4d: %s (%s vs %s)",
            idx + 1,
            game.game_id,
            game.black_engine,
            game.white_engine,
        )
    if len(schedule) > 10:
        logger.debug("  ... and %d more games", len(schedule) - 10)
