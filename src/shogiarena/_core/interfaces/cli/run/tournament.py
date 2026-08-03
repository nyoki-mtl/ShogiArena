"""Implementation of the ``shogiarena run tournament`` command."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunManifestSealError,
)
from shogiarena._core.contexts.tournament.application.entrypoints import (
    create_tournament_run_storage,
    run_tournament_session,
)
from shogiarena._core.interfaces.cli.config_loader import load_tournament_run_config, load_tournament_run_config_payload
from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.settings.facade import current_settings
from shogiarena._core.platform.settings.loader import validate_overlays
from shogiarena._core.shared.kernel.remote_provisioning import (
    remote_provisioning_scope,
    resolve_remote_provisioning_policy,
)
from shogiarena._core.shared.kernel.run_manifest_reader import read_sealed_manifest_resume_hash
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize

from .base_run import BaseRunCommand, ResumeCandidate

LOGGER = logging.getLogger("shogiarena.cli.run_tournament")


class _TournamentSectionPort(Protocol):
    scheduler: str
    games_per_pair: int
    seed: int
    baseline_count: int


class _TournamentRunConfigPort(Protocol):
    tournament: _TournamentSectionPort
    engines: object


async def run_tournament_command(
    *,
    config_file: Path,
    should_skip_resume: bool,
    is_dry_run: bool,
    should_validate_only: bool = False,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    config_base_dir: Path | None = None,
    config_payload: Mapping[str, object] | None = None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    # Helper to allow standalone usage if needed (though discouraged now)
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())
    payload_source_path = config_file if config_file.exists() else None

    config = (
        load_tournament_run_config_payload(
            config_payload,
            base_dir=config_base_dir or Path.cwd(),
            source_path=payload_source_path,
        )
        if config_payload is not None
        else load_tournament_run_config(config_file)
    )
    validate_overlays(current_settings())
    instance_pool = cmd.resolve_instance_pool(config.instances)
    cmd.validate_engine_instance_references(config.engines, instance_pool)

    if should_validate_only:
        LOGGER.info("Validated tournament config: %s", config_file)
        return

    if not is_dry_run:
        try:
            provisioning_policy = resolve_remote_provisioning_policy(provision_mode)
        except ValueError as exc:
            raise CliError(f"Invalid remote provisioning configuration: {exc}") from exc

    cmd.apply_git_worktree(config.engines, git_worktree)
    cmd.clear_hash_cache(config)

    run_dir = cmd.resolve_run_dir_path(
        config_file,
        config.output_dir / "tournament",
        experiment_name,
        run_dir_override,
        schedule_hash=config.get_schedule_hash(),
    )

    resume_dir = cmd.prompt_resume(
        config_file=config_file,
        output_dir=config.output_dir / "tournament",
        experiment_name=experiment_name,
        run_dir_override=run_dir_override,
        should_skip_resume=should_skip_resume,
        is_dry_run=is_dry_run,
        scan_candidates_fn=_scan_tournament_candidates,
        match_hash=None if config.has_unresolved_artifacts() else config.get_resume_hash(),
        group_hash=config.get_schedule_hash(),
    )
    if resume_dir is not None:
        run_dir = resume_dir

    if is_dry_run:
        _dry_run_schedule(config)
        return

    cmd.announce_run_directory(run_dir)
    root = build_default_root()
    storage = create_tournament_run_storage(run_dir, runtime=root.tournament_runtime)

    try:
        with remote_provisioning_scope(provisioning_policy):
            await run_tournament_session(
                config,
                storage=storage,
                should_skip_resume=should_skip_resume,
                instance_pool=instance_pool,
                runtime=root.tournament_runtime,
            )
    except RunManifestSealError as exc:
        message = "Run manifest is not compatible with current config/provenance. Use --no-resume to start fresh."
        raise CliError(message) from exc


async def run_generate_command(
    *,
    config_file: Path,
    should_skip_resume: bool,
    is_dry_run: bool,
    should_validate_only: bool = False,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    config_base_dir: Path | None = None,
    config_payload: Mapping[str, object] | None = None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())
    payload_source_path = config_file if config_file.exists() else None

    config = (
        load_tournament_run_config_payload(
            config_payload,
            base_dir=config_base_dir or Path.cwd(),
            source_path=payload_source_path,
        )
        if config_payload is not None
        else load_tournament_run_config(config_file)
    )
    validate_overlays(current_settings())
    instance_pool = cmd.resolve_instance_pool(config.instances)
    cmd.validate_engine_instance_references(config.engines, instance_pool)

    if should_validate_only:
        LOGGER.info("Validated generate config: %s", config_file)
        return

    if not is_dry_run:
        try:
            provisioning_policy = resolve_remote_provisioning_policy(provision_mode)
        except ValueError as exc:
            raise CliError(f"Invalid remote provisioning configuration: {exc}") from exc

    cmd.apply_git_worktree(config.engines, git_worktree)
    cmd.clear_hash_cache(config)

    run_dir = cmd.resolve_run_dir_path(
        config_file,
        config.output_dir / "generate",
        experiment_name,
        run_dir_override,
        schedule_hash=config.get_schedule_hash(),
    )

    resume_dir = cmd.prompt_resume(
        config_file=config_file,
        output_dir=config.output_dir / "generate",
        experiment_name=experiment_name,
        run_dir_override=run_dir_override,
        should_skip_resume=should_skip_resume,
        is_dry_run=is_dry_run,
        scan_candidates_fn=_scan_tournament_candidates,
        match_hash=None if config.has_unresolved_artifacts() else config.get_resume_hash(),
        group_hash=config.get_schedule_hash(),
    )
    if resume_dir is not None:
        run_dir = resume_dir

    if is_dry_run:
        _dry_run_schedule(config)
        return

    cmd.announce_run_directory(run_dir)
    root = build_default_root()
    storage = create_tournament_run_storage(run_dir, runtime=root.tournament_runtime)

    try:
        with remote_provisioning_scope(provisioning_policy):
            await run_tournament_session(
                config,
                storage=storage,
                should_skip_resume=should_skip_resume,
                instance_pool=instance_pool,
                runtime=root.tournament_runtime,
            )
    except RunManifestSealError as exc:
        message = "Run manifest is not compatible with current config/provenance. Use --no-resume to start fresh."
        raise CliError(message) from exc


def _scan_tournament_candidates(group_dir: Path) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for entry in sorted(group_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        if not (entry.name.isdigit() and len(entry.name) == 14):
            continue
        state_path = entry / "state.json"
        if not state_path.exists():
            continue
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw_map = {str(key): json_serialize(value) for key, value in raw.items()}
        completed_count = coerce_int(raw_map.get("completed_games_count")) or 0
        total = raw_map.get("original_total_games") or raw_map.get("total_games") or 0
        updated_at = raw_map.get("updated_at") or raw_map.get("created_at") or "-"
        is_finished = coerce_bool(raw_map.get("is_finished"))
        resume_hash = read_sealed_manifest_resume_hash(entry / "manifest.json", logger=LOGGER)
        if resume_hash is None or coerce_optional_text(raw_map.get("resume_hash")) != resume_hash:
            LOGGER.info("Skipping resume candidate without matching sealed manifest: %s", entry)
            continue
        candidates.append(
            {
                "path": entry,
                "slug": entry.name,
                "completed": completed_count,
                "total": coerce_int(total) or 0,
                "updated_at": coerce_optional_text(updated_at) or "-",
                "is_finished": is_finished,
                "match_hash": resume_hash,
            }
        )
    return candidates


def _dry_run_schedule(config: _TournamentRunConfigPort) -> None:
    logger = LOGGER
    scheduler = config.tournament.scheduler or "round_robin"
    games_per_pair = config.tournament.games_per_pair
    seed = config.tournament.seed
    baseline_count = config.tournament.baseline_count
    estimated = _estimate_total_games(
        scheduler=scheduler,
        games_per_pair=games_per_pair,
        baseline_count=baseline_count,
        engines=config.engines,
    )
    logger.info(
        "DRY RUN: scheduler=%s games_per_pair=%d seed=%d estimated_games=%s",
        scheduler,
        games_per_pair,
        seed,
        estimated if estimated is not None else "unknown",
    )


def _estimate_total_games(
    *,
    scheduler: str,
    games_per_pair: int,
    baseline_count: int,
    engines: object,
) -> int | None:
    engine_list = engines if isinstance(engines, list) else None
    if engine_list is None:
        return None
    num_engines = len(engine_list)
    if num_engines <= 0:
        return 0

    if scheduler == "selfplay":
        return max(0, games_per_pair)
    if scheduler == "round_robin":
        if num_engines < 2:
            return 0
        return num_engines * (num_engines - 1) // 2 * max(0, games_per_pair)
    if scheduler == "gauntlet":
        if num_engines < 2:
            return 0
        normalized_baseline = max(1, baseline_count)
        baseline_slots = min(normalized_baseline, num_engines - 1)
        challengers = num_engines - baseline_slots
        return baseline_slots * challengers * max(0, games_per_pair)
    return None
