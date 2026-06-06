"""Implementation of the ``shogiarena run spsa`` command."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunManifestSealError,
)
from shogiarena._core.contexts.spsa.application.entrypoints import (
    build_spsa_run_config,
    create_spsa_run_storage,
    run_spsa_session,
    spsa_engine_trace_logger_names,
)
from shogiarena._core.interfaces.cli.config_file_loaders import parse_spsa_config_file
from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.platform.settings.facade import current_settings
from shogiarena._core.platform.settings.loader import validate_overlays
from shogiarena._core.shared.kernel.run_manifest_reader import read_sealed_manifest_resume_hash
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize

from .base_run import BaseRunCommand, ResumeCandidate

LOGGER = logging.getLogger("shogiarena.cli.run_spsa")


async def run_spsa_command(
    *,
    config_file: Path,
    should_trace_engine: bool,
    is_dry_run: bool,
    should_validate_only: bool = False,
    should_skip_resume: bool,
    provision_mode: str,
    git_worktree: str,
    experiment_name: str | None,
    run_dir_override: str | None,
    config_payload: Mapping[str, object] | None = None,
    base_cmd: BaseRunCommand | None = None,
) -> None:
    logger = LOGGER
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())

    root = build_default_root()

    effective_payload = config_payload if config_payload is not None else parse_spsa_config_file(config_file)
    cfg = build_spsa_run_config(
        effective_payload,
        source_path=None if config_payload is not None else config_file,
        runtime=root.spsa_runtime,
    )
    validate_overlays(current_settings())

    if should_validate_only:
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
        should_skip_resume=should_skip_resume,
        is_dry_run=is_dry_run,
        scan_candidates_fn=_scan_spsa_candidates,
        match_hash=None,  # SPSA config hashing not strictly enforced yet
    )
    if resume_dir is not None:
        run_dir = resume_dir

    instance_pool = cmd.resolve_instance_pool(cfg.instances)

    # SPSA specific: combine baseline and tuned for common ops
    all_engines = cfg.baseline + cfg.tuned

    cmd.apply_git_worktree(all_engines, git_worktree)

    if should_trace_engine or logging.getLogger().level == logging.DEBUG:
        for name in spsa_engine_trace_logger_names(runtime=root.spsa_runtime):
            logging.getLogger(name).setLevel(logging.DEBUG)
        logger.info("[SPSA] Engine trace enabled: logging all USI commands and outputs")

    if instance_pool and provision_mode == "force" and not is_dry_run:
        await cmd.provision_engines(all_engines, instance_pool)

    if is_dry_run:
        logger.info("DRY RUN MODE: Validated SPSA config")
        base_names = [e.name for e in cfg.baseline]
        tuned_names = [e.name for e in cfg.tuned]
        logger.debug("Experiment: %s", cfg.experiment_name)
        logger.debug("Run directory: %s", run_dir)
        logger.debug("Baseline: %s", base_names)
        logger.debug("Tuned: %s", tuned_names)
        logger.debug("Space path: %s", cfg.space_path)
        logger.debug("Start SFENs: %s", cfg.start_sfens_path)
        return

    storage = create_spsa_run_storage(run_dir, runtime=root.spsa_runtime)
    try:
        await run_spsa_session(
            cfg,
            storage=storage,
            should_skip_resume=should_skip_resume,
            instance_pool=instance_pool,
            runtime=root.spsa_runtime,
        )
    except RunManifestSealError as exc:
        message = "Run manifest is not compatible with current config/provenance. Use --no-resume to start fresh."
        raise CliError(message) from exc


def _scan_spsa_candidates(group_dir: Path) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for entry in sorted(group_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        if not (entry.name.isdigit() and len(entry.name) == 14):
            continue

        state_path = entry / "state.json"

        if not state_path.exists():
            continue

        updated_at = "-"
        is_finished = False
        completed = 0
        total = 0

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if isinstance(state, dict):
            state_map = {str(key): json_serialize(value) for key, value in state.items()}
            manifest_resume_hash = read_sealed_manifest_resume_hash(entry / "manifest.json", logger=LOGGER)
            if (
                manifest_resume_hash is None
                or coerce_optional_text(state_map.get("resume_hash")) != manifest_resume_hash
            ):
                LOGGER.info("Skipping resume candidate without matching sealed manifest: %s", entry)
                continue
            updated_at = state_map.get("updated_at") or state_map.get("created_at") or updated_at
            completed_val = state_map.get("completed_updates")
            total_val = state_map.get("total_updates")
            completed = coerce_int(completed_val) or 0
            total = coerce_int(total_val) or 0
            is_finished = coerce_bool(state_map.get("is_finished", False))

        candidates.append(
            {
                "path": entry,
                "slug": entry.name,
                "completed": completed,
                "total": total,
                "updated_at": coerce_optional_text(updated_at) or "-",
                "is_finished": is_finished,
                "match_hash": None,
            }
        )
    return candidates
