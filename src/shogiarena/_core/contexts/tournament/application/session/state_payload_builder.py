"""Run-state payload builders for tournament runner."""

from __future__ import annotations

from datetime import UTC, datetime

from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    TournamentStateSaveContext,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


def _add_schedule_metadata_fields(entry: JsonObject, spec: GameSpec) -> None:
    for key, value in spec.to_schedule_metadata().items():
        if key in {"schema_version", "round_num", "initial_sfen"}:
            continue
        entry[key] = value


def _build_cancelled_entries(ctx: TournamentStateSaveContext) -> list[JsonObject]:
    cancelled_entries: list[JsonObject] = []
    for spec in ctx.state.cancelled_specs.values():
        entry: JsonObject = {
            "game_id": spec.game_id,
            "black": spec.black_engine,
            "white": spec.white_engine,
            "round": spec.round_num,
            "sfen": spec.initial_sfen,
        }
        _add_schedule_metadata_fields(entry, spec)
        assignment_payload = ctx.serialize_assignment_override(spec)
        if assignment_payload:
            entry["assignment"] = assignment_payload
        if bool(spec.should_require_install):
            entry["should_require_install"] = True
        cancelled_entries.append(entry)
    return cancelled_entries


def _build_instance_overrides(ctx: TournamentStateSaveContext) -> JsonObject:
    overrides: JsonObject = {}
    for spec in ctx.state.game_schedule:
        assignment_payload = ctx.serialize_assignment_override(spec)
        if assignment_payload:
            overrides[spec.game_id] = assignment_payload
    for spec in ctx.state.cancelled_specs.values():
        assignment_payload = ctx.serialize_assignment_override(spec)
        if assignment_payload:
            overrides[spec.game_id] = assignment_payload
    return overrides


def build_run_state_payload(
    ctx: TournamentStateSaveContext,
    *,
    is_finished: bool = False,
) -> JsonObject:
    """Build persisted run-state payload from current runner state."""

    now_iso = datetime.now(UTC).isoformat()
    if ctx.schedule_hash is None or ctx.resume_hash is None:
        raise ValueError("state.json requires sealed manifest hashes")

    if ctx.is_generate_run():
        run_state: JsonObject = {
            "schedule_hash": ctx.schedule_hash,
            "resume_hash": ctx.resume_hash,
            "total_games": len(ctx.state.game_schedule),
            "completed_games_count": len(ctx.state.completed_game_ids),
            "cancelled_games_count": len(ctx.state.cancelled_game_ids),
            "is_finished": is_finished,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        openbench_snap = ctx.openbench.snapshot_state()
        if openbench_snap is not None:
            run_state["openbench_state"] = openbench_snap
        return run_state

    run_state = {
        "schedule_hash": ctx.schedule_hash,
        "resume_hash": ctx.resume_hash,
        "total_games": len(ctx.state.game_schedule),
        "completed_games_count": len(ctx.state.completed_game_ids),
        "cancelled_game_ids": list(ctx.state.cancelled_game_ids),
        "cancelled_games": [],
        "original_total_games": ctx.state.original_total_games
        or (len(ctx.state.game_schedule) + len(ctx.state.cancelled_game_ids)),
        "game_display_order": dict(ctx.state.game_display_order),
        "is_finished": is_finished,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    if ctx.state.sprt is not None:
        run_state["sprt_state"] = ctx.state.sprt.to_snapshot()

    # timeout breaker の counter（task 0052 / review M3）。永続化しないと resume のたびに
    # ゼロへ戻り、pause / resume を繰り返すことで安全停止の閾値を実質的に回避できてしまう。
    if ctx.state.invalid_timeouts_by_origin:
        run_state["invalid_timeouts_by_origin"] = dict(sorted(ctx.state.invalid_timeouts_by_origin.items()))
    if ctx.state.consecutive_invalid_timeouts_by_origin:
        run_state["consecutive_invalid_timeouts_by_origin"] = dict(
            sorted(ctx.state.consecutive_invalid_timeouts_by_origin.items())
        )
    openbench_snap2 = ctx.openbench.snapshot_state()
    if openbench_snap2 is not None:
        run_state["openbench_state"] = openbench_snap2

    run_state["cancelled_games"] = _build_cancelled_entries(ctx)

    overrides = _build_instance_overrides(ctx)
    if overrides:
        # Persist per-game instance choices so a resume or dashboard refresh can restore the selection.
        run_state["game_instance_overrides"] = overrides

    return run_state


__all__ = ["build_run_state_payload"]
