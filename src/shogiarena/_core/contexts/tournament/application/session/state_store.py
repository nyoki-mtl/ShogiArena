"""Persistence and resume handling for tournament runner state."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.tournament.application.session.state_payload_builder import build_run_state_payload
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    AssignmentOverride,
    AssignmentOverridePayload,
    TournamentStateSaveContext,
    TournamentStateSetupContext,
    normalize_schedule_seed,
)
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_tournament_run_state_boundary,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.run_manifest_reader import read_sealed_manifest_resume_hash
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import SprtServicePort

logger = logging.getLogger(__name__)


def _normalize_assignment_override_payload(raw_payload: object) -> AssignmentOverridePayload:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, str):
        return raw_payload
    if isinstance(raw_payload, Mapping):
        result: AssignmentOverride = {}
        for key, value in raw_payload.items():
            result[str(key)] = json_serialize(value)  # type: ignore[literal-required]
        return result
    return None


class TournamentSessionStateStore:
    """Extracted run-state save/resume flow used by tournament runner."""

    async def try_setup_tournament(self, ctx: TournamentStateSetupContext) -> bool:
        """Setup new tournament or resume existing run state."""

        rd = ctx.run_dir
        run_state_path = rd / "state.json"

        if run_state_path.exists() and not ctx.run_options.should_skip_resume:
            return await self.try_resume_tournament(ctx)

        await self.setup_new_tournament(ctx)
        return False

    async def setup_new_tournament(self, ctx: TournamentStateSetupContext) -> None:
        """Setup a new tournament schedule."""

        logger.debug("Setting up new tournament in %s", ctx.run_dir)

        # Warn about flip_policy choices for statistical robustness
        flip = ctx.config.rules.initial_positions.flip_policy
        # Enforce: game_order=pairwise requires flip_policy=pair_both
        if ctx.config.tournament.game_order == "pairwise" and flip != "pair_both":
            raise ValueError("game_order='pairwise' requires initial_positions.flip_policy='pair_both'")
        # Warn if baseline_count specified but scheduler is not gauntlet
        if ctx.config.tournament.scheduler != "gauntlet":
            baseline_count = coerce_int(ctx.config.tournament.baseline_count) or 0
            if baseline_count != 1:
                logger.warning(
                    "tournament.baseline_count is specified but scheduler is not 'gauntlet'; the value will be ignored."
                )

        if flip != "pair_both":
            if ctx.config.sprt is not None:
                logger.warning(
                    "flip_policy is '%s'. For SPRT, flip_policy='pair_both' is recommended for paired samples.",
                    flip,
                )
            else:
                logger.debug(
                    "flip_policy is '%s'. Pentanomial stats will be based on available pairs only (reference).",
                    flip,
                )
        else:
            # pair_both with odd games_per_pair produces one-sided leftovers
            games_per_pair = coerce_int(ctx.config.tournament.games_per_pair) or 0
            if (games_per_pair % 2) == 1:
                logger.warning(
                    "games_per_pair is odd with flip_policy='pair_both'. "
                    "One color per pair will be unpaired; consider even number."
                )

        # Generate game schedule
        ctx.state.game_schedule = ctx.scheduler.generate_schedule(
            engines=list(ctx.config.engines),
            games_per_pair=ctx.config.tournament.games_per_pair,
            seed=normalize_schedule_seed(ctx.config.tournament.seed),
            initial_positions=ctx.config.rules.initial_positions,
        )
        ctx.state.game_schedule = ctx.reorder_and_shuffle(ctx.state.game_schedule)

        logger.debug("Generated %s games", len(ctx.state.game_schedule))

        # Save run state
        ctx.state.completed_game_summaries.clear()
        ctx.reset_schedule_tracking()
        self.save_run_state(ctx.build_save_context())
        ctx.write_schedule_file(ctx.state.game_schedule)
        ctx.notify_schedule_available()

    async def try_resume_tournament(self, ctx: TournamentStateSetupContext) -> bool:
        """Resume tournament schedule from persisted run-state."""

        logger.debug("Attempting to resume tournament")

        # Load and validate run state
        rd = ctx.run_dir
        run_state_path = rd / "state.json"
        try:
            with open(run_state_path, encoding="utf-8") as f:
                raw = json.load(f)
            saved_state = parse_tournament_run_state_boundary(raw, path=str(run_state_path))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, ContractParseError):
            logger.warning("Failed to parse state.json, cannot resume", exc_info=True)
            return False

        # Validate resume hash. This includes logical schedule, provenance,
        # SPRT test definition, and resume contract version.
        sealed_resume_hash = read_sealed_manifest_resume_hash(ctx.run_dir / "manifest.json", logger=logger)
        if sealed_resume_hash is None:
            logger.warning("Run manifest is not provenance sealed, cannot resume. Use --no-resume to start fresh.")
            return False
        if ctx.resume_hash is None or ctx.resume_hash != sealed_resume_hash:
            logger.warning("Current run provenance does not match sealed manifest, cannot resume.")
            return False
        if saved_state.get("resume_hash") != sealed_resume_hash:
            logger.warning("Configuration changed, cannot resume. Use --no-resume to start fresh.")
            return False

        # Load completed games from game.db, which is the authoritative source.
        ctx.state.completed_game_ids = self._load_completed_game_ids(ctx)
        sprt_state = saved_state.get("sprt_state")
        if ctx.state.sprt is not None and sprt_state is not None:
            try:
                restored_sprt: SprtServicePort = type(ctx.state.sprt).from_snapshot(sprt_state)
                ctx.state.sprt = restored_sprt
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("Failed to restore SPRT state: %s", exc)
        openbench_state = saved_state.get("openbench_state")
        if openbench_state is not None:
            ctx.openbench.restore_state(openbench_state)

        ctx.state.completed_game_summaries = {}

        persisted_schedule = self._load_persisted_schedule(ctx.run_dir / "schedule.json")
        if persisted_schedule is not None:
            ctx.state.game_schedule = persisted_schedule
        else:
            ctx.state.game_schedule = ctx.scheduler.generate_schedule(
                engines=list(ctx.config.engines),
                games_per_pair=ctx.config.tournament.games_per_pair,
                seed=normalize_schedule_seed(ctx.config.tournament.seed),
                initial_positions=ctx.config.rules.initial_positions,
            )
            ctx.state.game_schedule = ctx.reorder_and_shuffle(ctx.state.game_schedule)

        if saved_state.get("game_display_order"):
            ctx.state.game_display_order = saved_state["game_display_order"]
        else:
            ctx.reset_display_order()

        remaining = len([m for m in ctx.state.game_schedule if m.game_id not in ctx.state.completed_game_ids])
        logger.debug(
            "Resuming tournament: %s completed, %s remaining games", len(ctx.state.completed_game_ids), remaining
        )

        cancelled_ids = set(saved_state.get("cancelled_game_ids", []))
        cancelled_games = saved_state.get("cancelled_games", [])
        payload_by_id = {entry.get("game_id", ""): entry for entry in cancelled_games if isinstance(entry, dict)}
        ctx.state.cancelled_specs = {}
        retained_schedule: list[GameSpec] = []
        for spec in ctx.state.game_schedule:
            if spec.game_id in cancelled_ids:
                entry = payload_by_id.get(spec.game_id)
                if entry is not None:
                    reconstructed = GameSpec(
                        black_engine=entry.get("black") or spec.black_engine,
                        white_engine=entry.get("white") or spec.white_engine,
                        initial_sfen=entry.get("sfen") or spec.initial_sfen,
                        game_id=str(spec.game_id),
                        round_num=entry.get("round", spec.round_num or 0),
                    )
                    assignment_payload = entry.get("assignment")
                    ctx.apply_assignment_override(
                        reconstructed,
                        _normalize_assignment_override_payload(assignment_payload),
                    )
                    ctx.state.cancelled_specs[spec.game_id] = reconstructed
                else:
                    ctx.state.cancelled_specs[spec.game_id] = spec
                continue
            retained_schedule.append(spec)

        if cancelled_ids:
            ctx.state.game_schedule = retained_schedule

        ctx.ensure_display_order_for_specs(ctx.state.game_schedule)
        ctx.ensure_display_order_for_specs(ctx.state.cancelled_specs.values())

        ctx.state.cancelled_game_ids = cancelled_ids
        original_total_games = saved_state.get("original_total_games")
        if original_total_games is not None and original_total_games > 0:
            ctx.state.original_total_games = original_total_games
        else:
            ctx.state.original_total_games = len(ctx.state.game_schedule) + len(ctx.state.cancelled_game_ids)

        game_instance_overrides = saved_state.get("game_instance_overrides")
        if game_instance_overrides is not None:
            for spec in ctx.state.game_schedule:
                ctx.apply_assignment_override(
                    spec,
                    _normalize_assignment_override_payload(game_instance_overrides.get(spec.game_id)),
                )
            for spec in ctx.state.cancelled_specs.values():
                ctx.apply_assignment_override(
                    spec,
                    _normalize_assignment_override_payload(game_instance_overrides.get(spec.game_id)),
                )

        ctx.refresh_game_assignments()
        ctx.notify_schedule_available()

        return True

    def save_run_state(self, ctx: TournamentStateSaveContext, *, is_finished: bool = False) -> None:
        """Persist current run-state payload."""

        run_state = build_run_state_payload(ctx, is_finished=is_finished)
        run_state_path = ctx.run_dir / "state.json"
        write_json_atomic(run_state_path, run_state)

    @staticmethod
    def _load_completed_game_ids(ctx: TournamentStateSetupContext) -> set[str]:
        db_service = ctx.db_service
        if db_service is None:
            return set()
        game_type = "generate" if ctx.build_save_context().is_generate_run() else "arena"
        try:
            return {
                str(game.get("game_name"))
                for game in db_service.get_games_with_players(game_type=game_type)
                if game.get("game_name")
            }
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.warning("Failed to load completed games from game.db: %s", exc)
            return set()

    @staticmethod
    def _load_persisted_schedule(path: Path) -> list[GameSpec] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read schedule.json: %s", exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        games = raw.get("games")
        if not isinstance(games, list):
            return None
        schedule: list[GameSpec] = []
        for item in games:
            if not isinstance(item, Mapping):
                continue
            game_id = coerce_str(item.get("game_id"))
            if game_id is None:
                continue
            schedule.append(
                GameSpec(
                    black_engine=coerce_str(item.get("black")) or "",
                    white_engine=coerce_str(item.get("white")) or "",
                    initial_sfen=coerce_str(item.get("sfen")) or "startpos",
                    game_id=game_id,
                    round_num=coerce_int(item.get("round")) or 0,
                )
            )
        return schedule


__all__ = ["TournamentSessionStateStore"]
