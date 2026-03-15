"""Pure state and assignment helpers for tournament schedule operations.

All functions in this module operate only on ``TournamentRunnerState``
and/or ``GameSpec``.  They never require runtime context (``ctx``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Protocol

from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import AssignmentOverridePayload
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config protocol (minimal surface for assignment resolution)
# ---------------------------------------------------------------------------


class _EngineSpec(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def instance_id(self) -> object | None: ...


# ---------------------------------------------------------------------------
# State-only helpers (no ctx, no config)
# ---------------------------------------------------------------------------


def has_pending_games(state: TournamentRunnerState) -> bool:
    """Return True if the schedule contains games that are not yet completed or cancelled."""
    return any(
        spec.game_id not in state.completed_game_ids and spec.game_id not in state.cancelled_game_ids
        for spec in state.game_schedule
    )


def notify_schedule_available(state: TournamentRunnerState) -> None:
    """Signal that a new schedule is available for the run loop."""
    if not state.schedule_wait_event.is_set():
        state.schedule_wait_event.set()
    state.should_stop_when_idle = False


async def wait_for_new_schedule(state: TournamentRunnerState) -> None:
    """Block until a new schedule is signalled."""
    if state.schedule_wait_event.is_set():
        state.schedule_wait_event.clear()
        return
    logger.debug("Tournament drained; waiting for new schedule before resuming")
    state.session_phase = "waiting"
    await state.schedule_wait_event.wait()
    state.schedule_wait_event.clear()
    state.session_phase = "running"


def reset_display_order(state: TournamentRunnerState) -> None:
    """Reset display ordering to match the current schedule sequence."""
    state.game_display_order = {spec.game_id: index for index, spec in enumerate(state.game_schedule, start=1)}


def ensure_display_order_for_specs(state: TournamentRunnerState, specs: Iterable[GameSpec]) -> None:
    """Assign display order slots for specs that don't have one yet."""
    next_index = max(state.game_display_order.values(), default=0) + 1
    for spec in specs:
        gid = spec.game_id
        if gid not in state.game_display_order:
            state.game_display_order[gid] = next_index
            next_index += 1


def ensure_display_order_for_id(state: TournamentRunnerState, game_id: str) -> int:
    """Ensure display order for a single game id, returning the assigned order."""
    order = state.game_display_order.get(game_id)
    if order is None:
        order = max(state.game_display_order.values(), default=0) + 1
        state.game_display_order[game_id] = order
    return order


# ---------------------------------------------------------------------------
# Assignment helpers (pure spec / config operations)
# ---------------------------------------------------------------------------


def normalize_instance_id(value: object | None) -> str | None:
    """Normalize an instance id value, returning None for empty/auto/default."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.lower() in {"auto", "default"}:
        return None
    return normalized


def shared_override_label(spec: GameSpec) -> str | None:
    """Return a human-readable override label if both sides share an instance."""
    black = normalize_instance_id(spec.assigned_instance_black)
    white = normalize_instance_id(spec.assigned_instance_white)
    if black and white and black == white:
        return black
    if black and not white:
        return black
    if white and not black:
        return white
    if black or white:
        return "split"
    return None


def assignment_mode(spec: GameSpec) -> str:
    """Return the assignment mode string for a spec."""
    black = normalize_instance_id(spec.assigned_instance_black)
    white = normalize_instance_id(spec.assigned_instance_white)
    if black is None and white is None:
        return "auto"
    if black == white:
        return "shared"
    return "per_color"


def serialize_assignment_override(spec: GameSpec) -> JsonObject | None:
    """Serialize a spec's assignment override for persistence."""
    black = normalize_instance_id(spec.assigned_instance_black)
    white = normalize_instance_id(spec.assigned_instance_white)
    should_require_install = bool(spec.should_require_install)
    if black is None and white is None and not should_require_install:
        return None
    payload: JsonObject = {}
    if black is not None:
        payload["black"] = black
    if white is not None:
        payload["white"] = white
    if black is not None and white is not None and black == white:
        payload.setdefault("shared", black)
    if should_require_install:
        payload["should_require_install"] = True
    if (
        payload.get("black") is not None
        and payload.get("white") is not None
        and payload.get("black") != payload.get("white")
    ):
        payload["mode"] = "per_color"
    elif payload:
        payload["mode"] = "shared"
    return payload


def apply_assignment_override(spec: GameSpec, payload: AssignmentOverridePayload) -> None:
    """Apply an assignment override payload to a spec."""
    spec.assigned_instance_black = None
    spec.assigned_instance_white = None
    spec.should_require_install = False

    if payload is None:
        return
    if isinstance(payload, str):
        normalized = normalize_instance_id(payload)
        spec.assigned_instance_black = normalized
        spec.assigned_instance_white = normalized
        return
    payload_dict = payload

    shared = normalize_instance_id(payload_dict.get("shared"))
    black = normalize_instance_id(payload_dict.get("black"))
    white = normalize_instance_id(payload_dict.get("white"))
    mode = str(payload_dict.get("mode") or "").strip().lower()

    if mode == "shared" and shared is not None:
        spec.assigned_instance_black = shared
        spec.assigned_instance_white = shared
    else:
        if black is not None:
            spec.assigned_instance_black = black
        if white is not None:
            spec.assigned_instance_white = white
        if shared is not None and spec.assigned_instance_black is None and spec.assigned_instance_white is None:
            spec.assigned_instance_black = shared
            spec.assigned_instance_white = shared

    if bool(payload_dict.get("should_require_install")):
        spec.should_require_install = True


# ---------------------------------------------------------------------------
# Assignment helpers requiring config (engines list)
# ---------------------------------------------------------------------------


def _infer_game_assignment(config_engines: Sequence[_EngineSpec], game_spec: GameSpec) -> tuple[str | None, str | None]:
    """Infer default instance assignment for a game spec from engine config."""
    b_spec = next((engine for engine in config_engines if engine.name == game_spec.black_engine), None)
    w_spec = next((engine for engine in config_engines if engine.name == game_spec.white_engine), None)
    if b_spec is None or w_spec is None:
        return (None, None)
    b_id = normalize_instance_id(b_spec.instance_id) or "local"
    w_id = normalize_instance_id(w_spec.instance_id) or "local"
    return (b_id, w_id)


def resolve_assignment_for_spec(config_engines: Sequence[_EngineSpec], spec: GameSpec) -> dict[str, str | None]:
    """Resolve the effective assignment for a spec, combining defaults and overrides."""
    default_black, default_white = _infer_game_assignment(config_engines, spec)
    override_black = normalize_instance_id(spec.assigned_instance_black)
    override_white = normalize_instance_id(spec.assigned_instance_white)

    resolved_black = override_black if override_black is not None else default_black
    resolved_white = override_white if override_white is not None else default_white

    if resolved_black == resolved_white:
        combined = resolved_black
    else:
        if resolved_black is None and resolved_white is None:
            combined = None
        else:
            combined = "split"

    return {
        "black": resolved_black,
        "white": resolved_white,
        "combined": combined,
    }


def refresh_game_assignments(state: TournamentRunnerState, config_engines: Sequence[_EngineSpec]) -> None:
    """Rebuild the game_assignments map from current schedule and config."""
    assignments: dict[str, dict[str, str | None]] = {}
    for spec in state.game_schedule:
        assignments[spec.game_id] = resolve_assignment_for_spec(config_engines, spec)
    for spec in state.cancelled_specs.values():
        assignments.setdefault(spec.game_id, resolve_assignment_for_spec(config_engines, spec))
    state.game_assignments = assignments


# ---------------------------------------------------------------------------
# Composite helpers
# ---------------------------------------------------------------------------


def reset_schedule_tracking(state: TournamentRunnerState, config_engines: Sequence[_EngineSpec]) -> None:
    """Reset cancelled state, display order, and assignments for a fresh schedule."""
    state.cancelled_game_ids.clear()
    state.cancelled_specs.clear()
    reset_display_order(state)
    state.original_total_games = len(state.game_schedule)
    refresh_game_assignments(state, config_engines)


__all__ = [
    "apply_assignment_override",
    "assignment_mode",
    "ensure_display_order_for_id",
    "ensure_display_order_for_specs",
    "has_pending_games",
    "normalize_instance_id",
    "notify_schedule_available",
    "refresh_game_assignments",
    "reset_display_order",
    "reset_schedule_tracking",
    "resolve_assignment_for_spec",
    "serialize_assignment_override",
    "shared_override_label",
    "wait_for_new_schedule",
]
