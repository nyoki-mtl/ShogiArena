"""Schedule mutation and rescheduling workflows for tournament sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import random

from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_generation_runtime import (
    generate_schedule_for_seed,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    ensure_display_order_for_id,
    normalize_instance_id,
    notify_schedule_available,
    refresh_game_assignments,
    reset_schedule_tracking,
    resolve_assignment_for_spec,
    shared_override_label,
)
from shogiarena._core.contexts.tournament.application.session.schedule_snapshot_service import (
    ScheduleSnapshotService,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class ScheduleMutationService:
    """Schedule mutation operations requiring runtime context."""

    def __init__(self, *, snapshot_service: ScheduleSnapshotService | None = None) -> None:
        self._snapshot_service = snapshot_service or ScheduleSnapshotService()

    def write_schedule_file(
        self, state: TournamentRunnerState, ctx: TournamentScheduleContext, schedule: list[GameSpec]
    ) -> None:
        """Persist the provided schedule to the run directory for inspection."""

        if ctx.is_generate_run():
            return

        rd = ctx.run_dir
        schedule_path = rd / "game_schedule.json"
        active_instances: dict[str, str] = {}
        if ctx.instance_pool is not None:
            for instance in ctx.instance_pool.list_instances():
                for gid in instance.active_game_by_id.keys():
                    active_instances[gid] = instance.name

        def _status(game_id: str) -> str:
            if game_id in state.cancelled_game_ids:
                return "cancelled"
            if game_id in state.completed_game_ids:
                return "completed"
            if game_id in active_instances:
                return "running"
            return "pending"

        def _combined_assignment(game_id: str) -> str | None:
            if game_id in active_instances:
                return active_instances[game_id]
            entry = state.game_assignments.get(game_id)
            return entry.get("combined") if entry is not None else None

        def _resolved_assignment(game_id: str) -> tuple[str | None, str | None]:
            entry = state.game_assignments.get(game_id)
            if entry is not None:
                return entry.get("black"), entry.get("white")
            return (None, None)

        payload: list[JsonObject] = []
        for spec in schedule:
            resolved_black, resolved_white = _resolved_assignment(spec.game_id)
            payload.append(
                {
                    "game_id": spec.game_id,
                    "black": spec.black_engine,
                    "white": spec.white_engine,
                    "round": spec.round_num,
                    "sfen": spec.initial_sfen,
                    "status": _status(spec.game_id),
                    "assigned_instance": _combined_assignment(spec.game_id),
                    "assigned_override": shared_override_label(spec),
                    "assigned_override_black": normalize_instance_id(spec.assigned_instance_black),
                    "assigned_override_white": normalize_instance_id(spec.assigned_instance_white),
                    "should_require_install": bool(spec.should_require_install),
                    "resolved_instance_black": resolved_black,
                    "resolved_instance_white": resolved_white,
                }
            )

        if state.cancelled_specs:
            for spec in state.cancelled_specs.values():
                resolved_black, resolved_white = _resolved_assignment(spec.game_id)
                payload.append(
                    {
                        "game_id": spec.game_id,
                        "black": spec.black_engine,
                        "white": spec.white_engine,
                        "round": spec.round_num,
                        "sfen": spec.initial_sfen,
                        "status": "cancelled",
                        "assigned_instance": _combined_assignment(spec.game_id),
                        "assigned_override": shared_override_label(spec),
                        "assigned_override_black": normalize_instance_id(spec.assigned_instance_black),
                        "assigned_override_white": normalize_instance_id(spec.assigned_instance_white),
                        "should_require_install": bool(spec.should_require_install),
                        "resolved_instance_black": resolved_black,
                        "resolved_instance_white": resolved_white,
                    }
                )
        schedule_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def has_applied_pending_reschedule(
        self, state: TournamentRunnerState, ctx: TournamentScheduleContext
    ) -> bool:
        """Apply a queued reschedule request after the orchestrator drains."""

        pending_schedule: list[GameSpec] | None
        pending_seed: int | None
        async with state.reschedule_lock:
            pending_schedule = state.pending_reschedule
            pending_seed = state.pending_reschedule_seed
            state.pending_reschedule = None
            state.pending_reschedule_seed = None

        if pending_schedule is None:
            return False

        if pending_seed is not None:
            ctx.config.tournament.seed = pending_seed

        state.game_schedule = pending_schedule
        if state.completed_game_summaries:
            state.completed_game_summaries = {
                gid: summary
                for gid, summary in state.completed_game_summaries.items()
                if gid in state.completed_game_ids
            }
        reset_schedule_tracking(state, list(ctx.config.engines))
        self.write_schedule_file(state, ctx, state.game_schedule)
        ctx.save_run_state()

        if ctx.is_dashboard_enabled:
            await ctx.update_dashboard()

        notify_schedule_available(state)
        return True

    async def request_reschedule(
        self,
        state: TournamentRunnerState,
        ctx: TournamentScheduleContext,
        *,
        seed: str | None = None,
    ) -> JsonObject:
        """Queue a reschedule using the provided seed and stop the current run."""

        if seed is None:
            new_seed = random.randint(0, 2**32 - 1)
        else:
            seed_text = str(seed).strip()
            if not seed_text or seed_text.lower() == "auto":
                new_seed = random.randint(0, 2**32 - 1)
            else:
                try:
                    new_seed = int(seed_text)
                except ValueError:
                    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
                    new_seed = int(digest[:8], 16)

        completed_ids = set(state.completed_game_ids)
        completed_specs = [spec for spec in state.game_schedule if spec.game_id in completed_ids]
        pending_count = len(state.game_schedule) - len(completed_specs)
        if pending_count <= 0:
            raise RuntimeError("no pending games remain to reschedule")

        new_schedule = generate_schedule_for_seed(ctx, seed=str(new_seed))
        if len(new_schedule) < pending_count:
            raise RuntimeError("generated schedule shorter than pending games")

        new_pending = new_schedule[:pending_count]
        combined_schedule = completed_specs + new_pending

        should_apply_immediately = False
        orchestrator = None
        async with state.reschedule_lock:
            state.pending_reschedule = combined_schedule
            state.pending_reschedule_seed = new_seed
            orchestrator = ctx.orchestrator
            if orchestrator is None:
                should_apply_immediately = True

        if should_apply_immediately:
            await self.has_applied_pending_reschedule(state, ctx)
            snapshot = await self._snapshot_service.get_schedule_snapshot(state, ctx)
            return {
                "status": "applied",
                "pending_games": snapshot.get("pending_games", 0),
                "seed": str(new_seed),
            }

        ctx.stop_controller.request_stop(reason="reschedule")
        if orchestrator is not None:
            orchestrator.request_stop()

        snapshot = await self._snapshot_service.get_schedule_snapshot(state, ctx)
        return {
            "status": "scheduled",
            "pending_games": snapshot.get("pending_games", 0),
            "seed": str(new_seed),
        }

    async def cancel_pending_games(self, state: TournamentRunnerState, ctx: TournamentScheduleContext) -> JsonObject:
        """Cancel remaining pending games while keeping the session alive for future schedules."""

        active_ids: set[str] = set()
        pool = ctx.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                active_ids.update(instance.active_game_by_id.keys())

        async with state.reschedule_lock:
            remaining_specs = [
                spec
                for spec in state.game_schedule
                if spec.game_id not in state.completed_game_ids and spec.game_id not in active_ids
            ]
            state.pending_reschedule = None
            state.pending_reschedule_seed = None

        cancelled_ids = [spec.game_id for spec in remaining_specs]
        for spec in remaining_specs:
            ensure_display_order_for_id(state, spec.game_id)
            state.cancelled_game_ids.add(spec.game_id)
            state.cancelled_specs[spec.game_id] = spec
        if cancelled_ids:
            state.game_schedule = [spec for spec in state.game_schedule if spec.game_id not in cancelled_ids]

        state.original_total_games = max(
            state.original_total_games,
            len(state.game_schedule) + len(state.cancelled_game_ids),
        )
        refresh_game_assignments(state, list(ctx.config.engines))
        self.write_schedule_file(state, ctx, state.game_schedule)
        ctx.save_run_state()

        controller = ctx.stop_controller
        controller.request_stop(reason="cancelled")
        if ctx.orchestrator is not None:
            ctx.orchestrator.request_stop()

        if ctx.is_dashboard_enabled:
            await ctx.update_dashboard()

        return {
            "status": "cancelled",
            "cancelled_games": cancelled_ids,
            "cancelled_count": len(cancelled_ids),
            "running_games": list(active_ids),
            "total_games": state.original_total_games,
        }

    async def cancel_game(
        self, state: TournamentRunnerState, ctx: TournamentScheduleContext, game_id: str
    ) -> JsonObject:
        """Cancel a single pending game by its identifier."""

        pool = ctx.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                if game_id in instance.active_game_by_id:
                    raise RuntimeError(f"game {game_id} is currently running and cannot be cancelled")

        async with state.reschedule_lock:
            if game_id in state.completed_game_ids:
                raise RuntimeError(f"game {game_id} has already completed")
            if game_id in state.cancelled_game_ids:
                raise RuntimeError(f"game {game_id} has already been cancelled")

            spec_index: int | None = None
            for idx, spec in enumerate(state.game_schedule):
                if spec.game_id == game_id:
                    spec_index = idx
                    target_spec = spec
                    break
            else:
                target_spec = None

            if target_spec is None or spec_index is None:
                raise ValueError(f"game {game_id} not found in pending schedule")

            state.game_schedule.pop(spec_index)
            ensure_display_order_for_id(state, game_id)
            state.cancelled_game_ids.add(game_id)
            state.cancelled_specs[game_id] = target_spec

            state.original_total_games = max(
                state.original_total_games,
                len(state.game_schedule) + len(state.cancelled_game_ids),
            )

            refresh_game_assignments(state, list(ctx.config.engines))
            self.write_schedule_file(state, ctx, state.game_schedule)
            ctx.save_run_state()

        if ctx.is_dashboard_enabled:
            await ctx.update_dashboard()

        pending_count = sum(
            1
            for spec in state.game_schedule
            if spec.game_id not in state.completed_game_ids and spec.game_id not in state.cancelled_game_ids
        )

        return {
            "status": "cancelled",
            "cancelled_game": game_id,
            "pending_games": pending_count,
            "cancelled_count": len(state.cancelled_game_ids),
        }

    async def restore_game(
        self, state: TournamentRunnerState, ctx: TournamentScheduleContext, game_id: str
    ) -> JsonObject:
        """Restore a previously cancelled game back into the pending schedule."""

        pool = ctx.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                if game_id in instance.active_game_by_id:
                    raise RuntimeError(f"game {game_id} is currently running and cannot be restored")

        async with state.reschedule_lock:
            already_scheduled = any(spec.game_id == game_id for spec in state.game_schedule)
            restored_spec = state.cancelled_specs.get(game_id)
            if restored_spec is None or game_id not in state.cancelled_game_ids:
                if game_id in state.completed_game_ids:
                    raise RuntimeError(f"game {game_id} has already completed")
                if already_scheduled:
                    raise RuntimeError(f"game {game_id} is already scheduled")
                raise ValueError(f"game {game_id} is not a cancelled game")

            if already_scheduled:
                raise RuntimeError(f"game {game_id} is already scheduled")

            state.cancelled_game_ids.discard(game_id)
            state.cancelled_specs.pop(game_id, None)
            state.completed_game_ids.discard(game_id)
            state.completed_game_summaries.pop(game_id, None)

            display_order = state.game_display_order.get(game_id)
            insert_index = len(state.game_schedule)
            if display_order is not None:
                for idx, spec in enumerate(state.game_schedule):
                    other_order = state.game_display_order.get(spec.game_id)
                    if other_order is not None and other_order > display_order:
                        insert_index = idx
                        break

            state.game_schedule.insert(insert_index, restored_spec)

            effective_order = display_order
            if effective_order is None:
                effective_order = max(state.game_display_order.values(), default=insert_index) + 1
            state.game_display_order[game_id] = effective_order

            if ctx.tournament_orchestrator is not None:
                await ctx.tournament_orchestrator.enqueue_restored_game(restored_spec, effective_order)

            refresh_game_assignments(state, list(ctx.config.engines))
            self.write_schedule_file(state, ctx, state.game_schedule)
            ctx.save_run_state()
            notify_schedule_available(state)

        if ctx.is_dashboard_enabled:
            await ctx.update_dashboard()

        pending_count = sum(
            1
            for spec in state.game_schedule
            if spec.game_id not in state.completed_game_ids and spec.game_id not in state.cancelled_game_ids
        )

        return {
            "status": "restored",
            "restored_game": game_id,
            "pending_games": pending_count,
            "cancelled_count": len(state.cancelled_game_ids),
        }

    async def set_game_instance(
        self,
        state: TournamentRunnerState,
        ctx: TournamentScheduleContext,
        game_id: str,
        *,
        mode: str = "auto",
        shared_instance: str | None = None,
        black_instance: str | None = None,
        white_instance: str | None = None,
        should_require_install: bool = False,
    ) -> JsonObject:
        """Assign or clear preferred instances for a pending game."""

        normalized_mode = (mode or "auto").strip().lower()
        if normalized_mode not in {"auto", "shared", "per_color"}:
            raise ValueError(f"unsupported assignment mode: {mode}")

        shared_normalized = normalize_instance_id(shared_instance)
        black_normalized = normalize_instance_id(black_instance)
        white_normalized = normalize_instance_id(white_instance)

        if normalized_mode == "auto":
            target_black = None
            target_white = None
        elif normalized_mode == "shared":
            shared_value = shared_normalized or black_normalized or white_normalized
            target_black = shared_value
            target_white = shared_value
        else:
            target_black = black_normalized
            target_white = white_normalized
            if target_black is None and target_white is None and shared_normalized is not None:
                target_black = shared_normalized
                target_white = shared_normalized

        pool = ctx.instance_pool
        for candidate in (target_black, target_white):
            if candidate is None:
                continue
            if candidate == "local":
                if pool is not None and pool.get_instance("local") is None:
                    pool.ensure_local_instance()
                continue
            if pool is None:
                raise ValueError("instance overrides require an instance pool")
            if pool.get_instance(candidate) is None:
                raise ValueError(f"unknown instance '{candidate}'")

        async with state.reschedule_lock:
            if game_id in state.completed_game_ids:
                raise RuntimeError(f"game {game_id} has already completed")
            if game_id in state.cancelled_game_ids:
                raise RuntimeError(f"game {game_id} has been cancelled")

            target_spec: GameSpec | None = None
            for spec in state.game_schedule:
                if spec.game_id == game_id:
                    target_spec = spec
                    break

            if target_spec is None:
                raise ValueError(f"game {game_id} not found in pending schedule")

            if pool is not None:
                for instance in pool.list_instances():
                    if game_id in instance.active_game_by_id:
                        raise RuntimeError(f"game {game_id} is currently running and cannot be reassigned")

            target_spec.assigned_instance_black = target_black
            target_spec.assigned_instance_white = target_white
            target_spec.should_require_install = bool(should_require_install)
            refresh_game_assignments(state, list(ctx.config.engines))
            self.write_schedule_file(state, ctx, state.game_schedule)
            ctx.save_run_state()

        if ctx.is_dashboard_enabled:
            await ctx.update_dashboard()

        resolved = resolve_assignment_for_spec(list(ctx.config.engines), target_spec)

        return {
            "status": "assigned",
            "game_id": game_id,
            "mode": normalized_mode,
            "black_instance": target_spec.assigned_instance_black,
            "white_instance": target_spec.assigned_instance_white,
            "resolved_black": resolved.get("black"),
            "resolved_white": resolved.get("white"),
            "should_require_install": bool(target_spec.should_require_install),
        }


__all__ = ["ScheduleMutationService"]
