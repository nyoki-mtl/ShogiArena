"""Persistence and resume handling for tournament runner state."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

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
from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.run_manifest_reader import read_sealed_manifest_resume_hash
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import SprtServicePort
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import is_decisive_result

logger = logging.getLogger(__name__)


def _normalize_assignment_override_payload(raw_payload: object) -> AssignmentOverridePayload:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, str):
        return raw_payload
    if isinstance(raw_payload, Mapping):
        # 動的キーのため TypedDict へ直接代入できない。実体は同じ dict で、
        # 未知キーも従来どおり保持したうえで境界型として扱う。
        normalized = {str(key): json_serialize(value) for key, value in raw_payload.items()}
        return cast("AssignmentOverride", normalized)
    return None


class TournamentSessionStateStore:
    """Extracted run-state save/resume flow used by tournament runner."""

    def assert_no_submitted_openbench_results(self, run_dir: Path) -> None:
        """加算済みの OpenBench 結果を持つ run dir の破棄を拒否する。

        OpenBench の結果 POST は idempotency key を持たない加算 API である。
        送信済みカウンタごと state.json を消して 0 から再送すると、サーバ側の
        集計が二重に増える。--no-resume の cleanup 前に fail closed で止める。
        """

        run_state_path = run_dir / "state.json"
        if not run_state_path.exists():
            return

        try:
            with open(run_state_path, encoding="utf-8") as handle:
                raw = json.load(handle)
            saved_state = parse_tournament_run_state_boundary(raw, path=str(run_state_path))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, ContractParseError) as exc:
            raise RuntimeError(
                f"OpenBench is enabled but {run_state_path} could not be read ({exc}), so a fresh run cannot "
                "prove that no results were already submitted. Use a new empty run directory and a new "
                "OpenBench result assignment instead."
            ) from exc

        openbench_state = saved_state.get("openbench_state")
        if not isinstance(openbench_state, Mapping):
            return

        inflight = openbench_state.get("inflight_submission")
        submitted = openbench_state.get("submitted")
        # games は永続化されない派生値。trinomial だけを見ると、全局クラッシュした run の
        # crashes / timelosses だけの送信実績を「未送信」と誤判定するため、全カウンタを見る。
        submitted_total = 0
        if isinstance(submitted, Mapping):
            submitted_total = sum(coerce_int(value) or 0 for value in submitted.values())
        if inflight is None and submitted_total == 0:
            return

        test_id = openbench_state.get("claimed_test_id") or openbench_state.get("target_test_id")
        raise RuntimeError(
            "This run directory already submitted results to OpenBench, so discarding it would resend them "
            "from zero and double-count the test. The OpenBench API applies additive deltas without an "
            f"idempotency key. Reconcile test_id={test_id}, submitted={submitted}, inflight={inflight}. "
            "Use a new empty run directory and a new OpenBench result assignment instead."
        )

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
        except (OSError, json.JSONDecodeError, TypeError, ValueError, ContractParseError) as exc:
            raise RuntimeError(
                f"Failed to parse state.json for resume: {exc}. Use --no-resume to start a fresh run."
            ) from exc

        # Validate resume hash. This includes logical schedule, provenance,
        # SPRT test definition, and resume contract version.
        sealed_resume_hash = read_sealed_manifest_resume_hash(ctx.run_dir / "manifest.json", logger=logger)
        if sealed_resume_hash is None:
            raise RuntimeError(
                "Run manifest is not provenance sealed, cannot resume. Use --no-resume to start a fresh run."
            )
        if ctx.resume_hash is None or ctx.resume_hash != sealed_resume_hash:
            raise RuntimeError(
                "Current run provenance does not match sealed manifest, cannot resume. "
                "Use --no-resume to start a fresh run."
            )
        if saved_state.get("resume_hash") != sealed_resume_hash:
            raise RuntimeError("Configuration changed, cannot resume. Use --no-resume to start a fresh run.")

        # Load completed games from game.db, which is the authoritative source. Non-game terminal
        # records (ERROR/INVALID-like outcomes) remain completed but are not SPRT observations.
        completed_games = self._load_completed_games(ctx)
        ctx.state.completed_game_ids = {str(game.get("game_name")) for game in completed_games if game.get("game_name")}
        sprt_state = saved_state.get("sprt_state")
        if ctx.state.sprt is not None:
            if sprt_state is None:
                raise RuntimeError(
                    "SPRT state is missing from state.json, cannot resume. Use --no-resume to start a fresh run."
                )
            try:
                restored_sprt: SprtServicePort = type(ctx.state.sprt).from_snapshot(sprt_state)
                ctx.state.sprt = restored_sprt
            except (AttributeError, TypeError, ValueError) as exc:
                # Resume must reconstruct the exact prior test state; a corrupt/incompatible
                # SPRT snapshot cannot be silently dropped or the test would continue under
                # different statistics. Fail closed.
                raise RuntimeError(
                    f"Failed to restore SPRT state for resume: {exc}. Use --no-resume to start a fresh run."
                ) from exc
            restored_games = getattr(restored_sprt, "games_played", None)
            sampled_games = sum(1 for game in completed_games if is_decisive_result(game["result"]))
            if restored_games != sampled_games:
                raise RuntimeError(
                    "SPRT state and game.db disagree on completed game count, cannot resume. "
                    "Use --no-resume to start a fresh run."
                )
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
                    raw_moves = entry.get("opening_line_moves_usi")
                    line_moves = (
                        tuple(str(move) for move in raw_moves if isinstance(move, str))
                        if isinstance(raw_moves, list)
                        else ()
                    )
                    display_order = coerce_int(entry.get("display_order"))
                    # `round` は None を持ちうる（parser が明示的に許容している）。
                    # dict.get(key, default) はキー欠落時しか default を返さないため、
                    # None 明示のときに round_num=None が入ってしまうのを防ぐ。
                    round_num = coerce_int(entry.get("round"))
                    reconstructed = GameSpec(
                        black_engine=entry.get("black") or spec.black_engine,
                        white_engine=entry.get("white") or spec.white_engine,
                        initial_sfen=entry.get("sfen") or spec.initial_sfen,
                        game_id=str(spec.game_id),
                        round_num=round_num if round_num is not None else (spec.round_num or 0),
                        display_order=display_order if display_order is not None else spec.display_order,
                        pair_key=coerce_str(entry.get("pair_key")) or spec.pair_key,
                        pair_slot=coerce_int(entry.get("pair_slot"))
                        if entry.get("pair_slot") is not None
                        else spec.pair_slot,
                        pair_index=coerce_int(entry.get("pair_index"))
                        if entry.get("pair_index") is not None
                        else spec.pair_index,
                        matchup_key=coerce_str(entry.get("matchup_key")) or spec.matchup_key,
                        opening_line_id=coerce_str(entry.get("opening_line_id")) or spec.opening_line_id,
                        opening_line_moves_usi=line_moves or spec.opening_line_moves_usi,
                        opening_source=coerce_str(entry.get("opening_source")) or spec.opening_source,
                        opening_source_line_no=coerce_int(entry.get("opening_source_line_no"))
                        if entry.get("opening_source_line_no") is not None
                        else spec.opening_source_line_no,
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
    def _load_completed_games(ctx: TournamentStateSetupContext) -> list[GameRecordPlayers]:
        db_service = ctx.db_service
        if db_service is None:
            return []
        game_type = "generate" if ctx.build_save_context().is_generate_run() else "arena"
        try:
            return list(db_service.get_games_with_players(game_type=game_type))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            # game.db is the authoritative source of completed games on resume. Treating a read
            # failure as "zero completed" would re-run every game and corrupt the run. Fail closed.
            raise RuntimeError(
                f"Failed to load completed games from game.db for resume: {exc}. Use --no-resume to start a fresh run."
            ) from exc

    @staticmethod
    def _load_persisted_schedule(path: Path) -> list[GameSpec] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Failed to parse schedule.json for resume: {exc}. Use --no-resume to start a fresh run."
            ) from exc
        if not isinstance(raw, Mapping):
            raise RuntimeError(
                "Invalid schedule.json for resume: root must be an object. Use --no-resume to start fresh."
            )
        games = raw.get("games")
        if not isinstance(games, list):
            raise RuntimeError(
                "Invalid schedule.json for resume: games must be an array. Use --no-resume to start fresh."
            )
        if not games:
            raise RuntimeError(
                "Invalid schedule.json for resume: games must not be empty. Use --no-resume to start fresh."
            )
        schedule: list[GameSpec] = []
        game_ids: set[str] = set()
        for index, item in enumerate(games):
            if not isinstance(item, Mapping):
                raise RuntimeError(
                    f"Invalid schedule.json for resume: games[{index}] must be an object. "
                    "Use --no-resume to start fresh."
                )
            game_id = coerce_str(item.get("game_id"))
            if not game_id:
                raise RuntimeError(
                    f"Invalid schedule.json for resume: games[{index}].game_id is required. "
                    "Use --no-resume to start fresh."
                )
            if game_id in game_ids:
                raise RuntimeError(
                    f"Invalid schedule.json for resume: duplicate game_id {game_id!r}. Use --no-resume to start fresh."
                )
            game_ids.add(game_id)
            black_engine = coerce_str(item.get("black"))
            white_engine = coerce_str(item.get("white"))
            if not black_engine or not white_engine:
                raise RuntimeError(
                    f"Invalid schedule.json for resume: games[{index}] requires black and white engines. "
                    "Use --no-resume to start fresh."
                )
            raw_moves = item.get("opening_line_moves_usi")
            if raw_moves is not None and (
                not isinstance(raw_moves, list) or any(not isinstance(move, str) for move in raw_moves)
            ):
                raise RuntimeError(
                    f"Invalid schedule.json for resume: games[{index}].opening_line_moves_usi must be strings. "
                    "Use --no-resume to start fresh."
                )
            line_moves = (
                tuple(str(move) for move in raw_moves if isinstance(move, str)) if isinstance(raw_moves, list) else ()
            )
            spec = GameSpec(
                black_engine=black_engine,
                white_engine=white_engine,
                initial_sfen=coerce_str(item.get("sfen")) or "startpos",
                game_id=game_id,
                round_num=coerce_int(item.get("round")) or 0,
                display_order=coerce_int(item.get("display_order")),
                pair_key=coerce_str(item.get("pair_key")),
                pair_slot=coerce_int(item.get("pair_slot")),
                pair_index=coerce_int(item.get("pair_index")),
                matchup_key=coerce_str(item.get("matchup_key")),
                opening_line_id=coerce_str(item.get("opening_line_id")),
                opening_line_moves_usi=line_moves,
                opening_source=coerce_str(item.get("opening_source")),
                opening_source_line_no=coerce_int(item.get("opening_source_line_no")),
            )
            schedule.append(spec)
        return schedule


__all__ = ["TournamentSessionStateStore"]
