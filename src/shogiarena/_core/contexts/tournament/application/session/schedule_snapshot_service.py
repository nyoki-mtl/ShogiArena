"""Schedule snapshot projection for tournament sessions.

Produces the dashboard-facing schedule payload by combining state,
config, and instance pool information.
"""

from __future__ import annotations

from datetime import UTC, datetime

from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    assignment_mode,
    ensure_display_order_for_id,
    ensure_display_order_for_specs,
    normalize_instance_id,
    shared_override_label,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import is_strict_numeric


class ScheduleSnapshotService:
    """Produces schedule snapshot projections for the dashboard."""

    async def get_schedule_snapshot(self, state: TournamentRunnerState, ctx: TournamentScheduleContext) -> JsonObject:
        """Return a schedule summary with status for each game."""

        async with state.reschedule_lock:
            schedule_copy = list(state.game_schedule)
            completed_ids = set(state.completed_game_ids)
            pending_reschedule = state.pending_reschedule is not None
            seed = str(ctx.config.tournament.seed)

        engine_instance_defaults = dict(ctx.engine_instance_defaults)

        active_game_ids: set[str] = set()
        active_instances: dict[str, str] = {}
        active_side_instances: dict[str, dict[str, str]] = {}
        active_start_times: dict[str, float | datetime | str] = {}
        instance_kinds: dict[str, str] = {}
        pool = ctx.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                inst_name = instance.name
                instance_kinds[inst_name] = instance.type.value
                for gid, active in instance.active_game_by_id.items():
                    active_game_ids.add(gid)
                    previous = active_instances.get(gid)
                    if previous is not None and previous != inst_name:
                        if previous != "split":
                            active_instances[gid] = "split"
                    else:
                        active_instances[gid] = inst_name
                    if active.roles:
                        side_map = active_side_instances.setdefault(gid, {})
                        for role in active.roles:
                            if role.role in {"black", "white"}:
                                side_map[role.role] = inst_name
                    started_raw = active.started_ts_sec
                    if started_raw is not None:
                        if gid not in active_start_times:
                            active_start_times[gid] = started_raw
                        else:
                            existing = active_start_times[gid]
                            if (
                                not isinstance(existing, datetime)
                                and not isinstance(started_raw, datetime)
                                and is_strict_numeric(existing)
                                and is_strict_numeric(started_raw)
                            ):
                                active_start_times[gid] = min(existing, started_raw)

        ensure_display_order_for_specs(state, schedule_copy)
        ensure_display_order_for_specs(state, list(state.cancelled_specs.values()))

        def _instance_kind(instance_name: str | None) -> str | None:
            if instance_name is None:
                return None
            kind = instance_kinds.get(instance_name)
            if kind:
                return kind
            if instance_name == "local":
                return "local"
            return None

        items: list[JsonObject] = []
        for game_spec in schedule_copy:
            gid = game_spec.game_id
            display_order = ensure_display_order_for_id(state, gid)
            order_index = display_order - 1
            if gid in completed_ids:
                status = "completed"
            elif gid in active_game_ids:
                status = "running"
            else:
                status = "pending"

            side_map = active_side_instances.get(gid, {})
            assigned_entry = state.game_assignments.get(gid) or {}

            black_default = engine_instance_defaults.get(game_spec.black_engine)
            white_default = engine_instance_defaults.get(game_spec.white_engine)
            resolved_black = assigned_entry.get("black") or black_default
            resolved_white = assigned_entry.get("white") or white_default

            black_instance = side_map.get("black") or resolved_black
            white_instance = side_map.get("white") or resolved_white

            if gid in active_instances:
                assigned_candidate: str | None = active_instances[gid]
            else:
                assigned_candidate = assigned_entry.get("combined")
            if assigned_candidate in {None, "split"}:
                if black_instance and white_instance:
                    if black_instance == white_instance:
                        assigned_candidate = black_instance
                    elif assigned_candidate is None:
                        assigned_candidate = "split"
            elif (
                assigned_candidate
                and assigned_candidate != "split"
                and (black_instance and white_instance and black_instance != white_instance)
            ):
                assigned_candidate = "split"

            assigned_override = shared_override_label(game_spec)

            entry: JsonObject = {
                "order": order_index,
                "game_id": gid,
                "black": game_spec.black_engine,
                "white": game_spec.white_engine,
                "round": game_spec.round_num,
                "status": status,
                "assigned_instance": assigned_candidate,
                "assigned_override": assigned_override,
                "assigned_override_black": normalize_instance_id(game_spec.assigned_instance_black),
                "assigned_override_white": normalize_instance_id(game_spec.assigned_instance_white),
                "assigned_mode": assignment_mode(game_spec),
                "should_require_install": bool(game_spec.should_require_install),
                "resolved_instance_black": resolved_black,
                "resolved_instance_white": resolved_white,
                "black_instance": black_instance,
                "white_instance": white_instance,
                "black_instance_kind": _instance_kind(black_instance),
                "white_instance_kind": _instance_kind(white_instance),
                "initial_sfen": game_spec.initial_sfen,
            }

            summary = state.completed_game_summaries.get(gid) if gid in completed_ids else None
            start_time_value: str | None = None
            if summary:
                summary_start = summary.get("start_time")
                if summary_start and isinstance(summary_start, str):
                    start_time_value = summary_start
            if start_time_value is None:
                raw_started = active_start_times.get(gid)
                match raw_started:
                    case int() | float():
                        start_time_value = datetime.fromtimestamp(raw_started, tz=UTC).isoformat()
                    case datetime() as dt:
                        start_time_value = dt.astimezone(UTC).isoformat()
                    case str() if raw_started:
                        start_time_value = raw_started
            if summary:
                entry.update(
                    {
                        "start_time": summary.get("start_time"),
                        "game_result": summary.get("game_result"),
                        "total_plies": summary.get("total_plies"),
                        "end_time": summary.get("end_time"),
                    }
                )
            else:
                entry.update(
                    {
                        "game_result": None,
                        "total_plies": None,
                        "start_time": None,
                        "end_time": None,
                    }
                )

            if start_time_value is not None:
                entry["start_time"] = start_time_value

            items.append(entry)

        for cancelled_spec in state.cancelled_specs.values():
            assignment_entry = state.game_assignments.get(cancelled_spec.game_id) or {}
            black_default = engine_instance_defaults.get(cancelled_spec.black_engine)
            white_default = engine_instance_defaults.get(cancelled_spec.white_engine)
            resolved_black = assignment_entry.get("black") or black_default
            resolved_white = assignment_entry.get("white") or white_default
            assigned_default: str | None
            if resolved_black and resolved_white:
                if resolved_black == resolved_white:
                    assigned_default = resolved_black
                else:
                    assigned_default = "split"
            else:
                assigned_default = resolved_black or resolved_white
            display_order = ensure_display_order_for_id(state, cancelled_spec.game_id)
            order_index = display_order - 1
            items.append(
                {
                    "order": order_index,
                    "game_id": cancelled_spec.game_id,
                    "black": cancelled_spec.black_engine,
                    "white": cancelled_spec.white_engine,
                    "round": cancelled_spec.round_num,
                    "status": "cancelled",
                    "assigned_instance": assigned_default,
                    "assigned_override": shared_override_label(cancelled_spec),
                    "assigned_override_black": normalize_instance_id(cancelled_spec.assigned_instance_black),
                    "assigned_override_white": normalize_instance_id(cancelled_spec.assigned_instance_white),
                    "assigned_mode": assignment_mode(cancelled_spec),
                    "should_require_install": bool(cancelled_spec.should_require_install),
                    "resolved_instance_black": resolved_black,
                    "resolved_instance_white": resolved_white,
                    "black_instance": resolved_black,
                    "white_instance": resolved_white,
                    "black_instance_kind": _instance_kind(resolved_black),
                    "white_instance_kind": _instance_kind(resolved_white),
                    "initial_sfen": cancelled_spec.initial_sfen,
                }
            )

        completed_count = sum(1 for spec in schedule_copy if spec.game_id in completed_ids)
        running_count = len(active_game_ids)
        cancelled_count = len(state.cancelled_game_ids)
        original_total = state.original_total_games or (len(schedule_copy) + cancelled_count)
        active_total = max(original_total - cancelled_count, 0)
        pending_count = max(active_total - completed_count - running_count, 0)

        stop_ctrl = ctx.stop_controller
        session_state = state.session_phase
        waiting = session_state == "waiting"
        idle = waiting and pending_count == 0 and running_count == 0

        return {
            "total_games": active_total,
            "original_total_games": original_total,
            "completed_games": completed_count,
            "running_games": running_count,
            "pending_games": pending_count,
            "cancelled_games": cancelled_count,
            "seed": seed,
            "reschedule_pending": pending_reschedule,
            "is_running": ctx.orchestrator is not None,
            "session_state": session_state,
            "stop_requested": stop_ctrl.is_stop_requested,
            "stop_reason": stop_ctrl.reason,
            "stop_when_idle": state.should_stop_when_idle,
            "is_waiting": waiting,
            "is_idle": idle,
            "schedule": items,
        }


__all__ = ["ScheduleSnapshotService"]
