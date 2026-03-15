"""Run-state payload builders for tournament runner."""

from __future__ import annotations

from datetime import UTC, datetime

from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    TournamentStateSaveContext,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


def _build_summaries_for_state(ctx: TournamentStateSaveContext) -> dict[str, JsonObject]:
    summaries_for_state: dict[str, JsonObject] = {}
    for gid in ctx.state.completed_game_ids:
        summary = ctx.state.completed_game_summaries.get(gid)
        if not summary:
            continue
        summaries_for_state[gid] = {
            "game_result": summary.get("game_result"),
            "total_plies": summary.get("total_plies"),
            "start_time": summary.get("start_time"),
            "end_time": summary.get("end_time"),
        }
    return summaries_for_state


def _build_config_payload(ctx: TournamentStateSaveContext) -> JsonObject:
    config_payload: JsonObject = {
        "experiment_name": ctx.config.experiment_name,
        "engines": [str(e.name) for e in ctx.config.engines],
        "tournament": {
            "scheduler": ctx.config.tournament.scheduler,
            "games_per_pair": ctx.config.tournament.games_per_pair,
            "seed": ctx.config.tournament.seed,
        },
        "rules": ctx.build_rules_payload(),
    }
    sprt_conf = ctx.config.sprt
    if sprt_conf is not None:
        config_payload["sprt"] = sprt_conf.model_dump(mode="json")
    openbench_conf = ctx.config.openbench
    if openbench_conf is not None:
        config_payload["openbench"] = openbench_conf.model_dump(mode="json")
    records_output = ctx.config.records_output
    if records_output is not None:
        config_payload["records_output"] = records_output.model_dump(mode="json")
    return config_payload


def _build_cancelled_entries(ctx: TournamentStateSaveContext) -> list[JsonObject]:
    cancelled_entries: list[JsonObject] = []
    for spec in ctx.state.cancelled_specs.values():
        entry = {
            "game_id": spec.game_id,
            "black": spec.black_engine,
            "white": spec.white_engine,
            "round": spec.round_num,
            "sfen": spec.initial_sfen,
        }
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
    config_payload = _build_config_payload(ctx)

    if ctx.is_generate_run():
        run_state: JsonObject = {
            "config": config_payload,
            "schedule_hash": ctx.config.get_schedule_hash(),
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
        "config": config_payload,
        "schedule_hash": ctx.config.get_schedule_hash(),
        "total_games": len(ctx.state.game_schedule),
        "completed_game_ids": list(ctx.state.completed_game_ids),
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
    openbench_snap2 = ctx.openbench.snapshot_state()
    if openbench_snap2 is not None:
        run_state["openbench_state"] = openbench_snap2

    run_state["cancelled_games"] = _build_cancelled_entries(ctx)
    summaries_for_state = _build_summaries_for_state(ctx)
    if summaries_for_state:
        run_state["completed_game_summaries"] = summaries_for_state

    overrides = _build_instance_overrides(ctx)
    if overrides:
        # Persist per-game instance choices so a resume or dashboard refresh can restore the selection.
        run_state["game_instance_overrides"] = overrides

    return run_state


__all__ = ["build_run_state_payload"]
