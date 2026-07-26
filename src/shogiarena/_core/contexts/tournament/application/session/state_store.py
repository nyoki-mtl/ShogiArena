"""Persistence and resume handling for tournament runner state."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

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
from shogiarena._core.shared.kernel.sprt_ingestion import normalize_sprt_observation
from shogiarena._core.shared.kernel.sprt_models import SPRT_MODEL_GSPRT_PENTANOMIAL
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    is_decisive_result,
    round_index_from_game_name,
)
from shogiarena._core.shared.kernel.timeout_breaker import rebuild_timeout_breaker_counters

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
            self._reconcile_sprt_with_completed_games(ctx, restored_sprt, completed_games=completed_games)
        self._restore_timeout_breaker_counters(ctx, saved_state, completed_games=completed_games)

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

        # validation と state restoration がすべて成功した時点で、前回の terminal marker を
        # 無効化する（task 0052 / decisions.md Decision 10）。resume 直後に crash しても
        # 完了済み run と誤表示されないよう、新規 dispatch より前に消す。
        # resume を拒否した経路ではここへ到達しないので、既存 artifact の bytes は変わらない。
        # ``tournament_results.json`` と ``summary_btd.json`` は terminal marker として
        # 読まれない derived cache なので対象外（finalize で必ず上書きされる）。
        self._invalidate_terminal_markers(ctx.run_dir)

        return True

    @staticmethod
    def _invalidate_terminal_markers(run_dir: Path) -> None:
        """完了済み run を resume する際に、前回の terminal marker を消す。"""

        for name in ("completion_status.json", "completed.flag"):
            path = run_dir / name
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                # 消せない marker を残したまま dispatch すると、途中状態を完了済みに見せる。
                raise RuntimeError(
                    f"Failed to invalidate the stale terminal marker {name} before resuming: {exc}. "
                    "Use --no-resume to start a fresh run."
                ) from exc
        logger.debug("Invalidated stale terminal markers in %s before resuming", run_dir)

    def save_run_state(self, ctx: TournamentStateSaveContext, *, is_finished: bool = False) -> None:
        """Persist current run-state payload."""

        run_state = build_run_state_payload(ctx, is_finished=is_finished)
        run_state_path = ctx.run_dir / "state.json"
        write_json_atomic(run_state_path, run_state)

    @staticmethod
    def _reconcile_sprt_with_completed_games(
        ctx: TournamentStateSetupContext,
        restored_sprt: SprtServicePort,
        *,
        completed_games: list[GameRecordPlayers],
    ) -> None:
        """復元した SPRT 状態を game.db に合わせる（decisions.md Decision 8）。

        ``game.db`` が正本。DB へ commit した後 ``save_run_state()`` の前に落ちると、
        state.json の SPRT は DB より数局遅れる。完全一致だけを許すと、この crash window で
        resume 不能になる（review H2）。breaker counter と同じく、遅れている分の
        DB suffix を **完了順に replay** して追いつかせる。

        decision をラッチした後の局は、標本ではなく ``late_games`` に入る（review H1）。
        したがって突き合わせるのは「標本 + 後着」であって標本だけではない。

        DB が state.json より **少ない** 場合は追いつかせようがないので fail closed にする。
        """
        tested_engine = ctx.state.sprt_pair[0] if ctx.state.sprt_pair is not None else None
        if tested_engine is None:
            # 検定対象ペアが決まらない構成（engine が 2 個でない）では、live でも
            # SPRT へ局を入れない。突き合わせる対象そのものが無い。
            return

        sampled = int(getattr(restored_sprt, "games_played", 0) or 0)
        late = int(getattr(restored_sprt, "late_games", 0) or 0)
        decisive = [game for game in completed_games if is_decisive_result(game["result"])]
        already = sampled + late

        if already == len(decisive):
            return
        if already > len(decisive):
            raise RuntimeError(
                "SPRT state is ahead of game.db "
                f"(sample={sampled}, late={late}, decisive in game.db={len(decisive)}), "
                "cannot resume. Use --no-resume to start a fresh run."
            )

        is_pentanomial = str(getattr(restored_sprt, "model", "")) == SPRT_MODEL_GSPRT_PENTANOMIAL
        replayed = 0
        for game in decisive[already:]:
            black = str(game["black_player"])
            white = str(game["white_player"])
            if tested_engine not in (black, white):
                # tested engine が絡まない局は live でも標本へ入らない。
                continue
            observation = normalize_sprt_observation(game["result"], is_tested_black=tested_engine == black)
            if is_pentanomial:
                game_name = game.get("game_name")
                round_index = round_index_from_game_name(str(game_name)) if game_name else None
                if round_index is None:
                    # pair slot を復元できない局を混ぜると、対にならない観測が残る。
                    raise RuntimeError(
                        f"Cannot replay pentanomial SPRT observation for game {game_name!r} "
                        "(no round index in the game name), cannot resume. "
                        "Use --no-resume to start a fresh run."
                    )
                restored_sprt.add_game_observation(
                    sfen=str(game.get("initial_sfen") or "startpos"),
                    pair_slot=round_index // 2,
                    is_tested_black=observation.is_tested_black,
                    tested_score=observation.tested_score,
                )
            else:
                restored_sprt.add_game_result(observation.trinomial_result)
            replayed += 1

            # live と同じく、**1 局入れるたび** に停止条件を評価してラッチする
            # （review 第5次 H2）。suffix を全部入れてから評価すると、途中で bound を
            # 越えた後の局まで標本に入り、確定したはずの decision を continue へ戻せる。
            if not getattr(restored_sprt, "is_decision_latched", False):
                if restored_sprt.is_finished() and restored_sprt.games_played >= ctx.state.sprt_min_games:
                    restored_sprt.latch_decision()
                    logger.info(
                        "SPRT reached its decision while replaying game.db (games=%d); "
                        "the remaining replayed games are counted as late",
                        restored_sprt.games_played,
                    )

        logger.warning(
            "Replayed %d SPRT observation(s) committed to game.db after the last state save "
            "(sample=%d, late=%d -> sample=%d, late=%d)",
            replayed,
            sampled,
            late,
            int(getattr(restored_sprt, "games_played", 0) or 0),
            int(getattr(restored_sprt, "late_games", 0) or 0),
        )

    @staticmethod
    def _restore_timeout_breaker_counters(
        ctx: TournamentStateSetupContext,
        saved_state: Mapping[str, Any],
        *,
        completed_games: list[GameRecordPlayers],
    ) -> None:
        """timeout breaker の counter を resume で復元する（task 0052 / review M3）。

        counter がゼロへ戻ると、pause / resume を繰り返すだけで安全停止の閾値を
        実質的に回避できてしまう。一方で state.json だけを見ると、DB へ commit した後
        `save_run_state()` の前に落ちた場合に stale な counter を復元してしまう
        （閾値の回避にも、逆に本来 reset された counter での誤停止にもなりうる）。

        そこで **game.db を正本** とし（decisions.md Decision 8）、state.json は
        「どこまでを counter に反映済みか」の起点として使う。

        - counter が state.json にあれば、それを起点に、保存時点より後の DB suffix を replay する。
        - counter が無い場合（1.1.0 より前の state.json）は DB 全体から再構築する。
        """
        saved_totals = saved_state.get("invalid_timeouts_by_origin") or {}
        saved_consecutive = saved_state.get("consecutive_invalid_timeouts_by_origin") or {}

        if not saved_totals and not saved_consecutive:
            totals, consecutive = rebuild_timeout_breaker_counters(completed_games)
            ctx.state.invalid_timeouts_by_origin = totals
            ctx.state.consecutive_invalid_timeouts_by_origin = consecutive
            if totals:
                logger.warning(
                    "Rebuilt timeout breaker counters from game.db (state.json predates them): %s",
                    dict(sorted(totals.items())),
                )
            return

        saved_completed = coerce_int(saved_state.get("completed_games_count"))
        if saved_completed is None:
            # 起点が分からない state.json で counter へ DB を重ねると二重計上になる。
            # 正本である DB から作り直す。
            logger.warning("state.json has breaker counters but no completed count; rebuilding from game.db")
            totals, consecutive = rebuild_timeout_breaker_counters(completed_games)
            ctx.state.invalid_timeouts_by_origin = totals
            ctx.state.consecutive_invalid_timeouts_by_origin = consecutive
            return

        suffix = completed_games[saved_completed:] if saved_completed >= 0 else []
        if saved_completed > len(completed_games):
            # DB が state.json より古い。counter だけを信じると DB に無い局を数えたままになるので、
            # DB から作り直す（正本は DB）。
            logger.warning(
                "state.json claims %d completed games but game.db has %d; "
                "rebuilding timeout breaker counters from game.db",
                saved_completed,
                len(completed_games),
            )
            totals, consecutive = rebuild_timeout_breaker_counters(completed_games)
            ctx.state.invalid_timeouts_by_origin = totals
            ctx.state.consecutive_invalid_timeouts_by_origin = consecutive
            return

        totals, consecutive = rebuild_timeout_breaker_counters(
            suffix,
            totals={str(k): int(v) for k, v in saved_totals.items()},
            consecutive={str(k): int(v) for k, v in saved_consecutive.items()},
        )
        ctx.state.invalid_timeouts_by_origin = totals
        ctx.state.consecutive_invalid_timeouts_by_origin = consecutive
        if suffix:
            logger.warning(
                "Reconciled timeout breaker counters with %d game(s) committed after the last state save: %s",
                len(suffix),
                dict(sorted(totals.items())),
            )

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
