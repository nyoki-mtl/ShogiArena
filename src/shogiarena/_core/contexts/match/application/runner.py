"""
Game runner for executing single games between two engines.

This module keeps the GameRunner facade thin while delegating runtime behavior
to focused mixins.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from shogiarena._core.contexts.match.domain.adjudication import AdjudicationConfig
from shogiarena._core.shared.kernel.time_control import TimeControlLimitsPort, coerce_time_control_limits

from .runner_finalize_mixin import GameRunnerFinalizeMixin
from .runner_loop_mixin import GameRunnerLoopMixin
from .runner_move_mixin import GameRunnerMoveMixin
from .runner_ponder_mixin import GameRunnerPonderMixin
from .runner_progress_mixin import GameRunnerProgressMixin
from .runner_run_mixin import GameRunnerRunMixin


class GameRunner(
    GameRunnerRunMixin,
    GameRunnerLoopMixin,
    GameRunnerPonderMixin,
    GameRunnerMoveMixin,
    GameRunnerFinalizeMixin,
    GameRunnerProgressMixin,
):
    """Executes a single game between two engines."""

    def __init__(
        self,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None = None,
        time_control_limits: TimeControlLimitsPort | None = None,
        adjudication_config: AdjudicationConfig | None = None,
        *,
        repetition_occurrences_to_draw: int = 2,
        on_engine_options: Callable[[str, Any, dict[str, str] | None], None] | None = None,
    ) -> None:
        """
        Initialize game runner.

        Args:
            progress_queue: Optional queue for progress updates (game_id, ply_index, description or None)
            time_control_limits: Global time control configuration (per-side overrides are passed to run_game)
            adjudication_config: Adjudication configuration (max plies and resign policies)
            repetition_occurrences_to_draw: Number of times the same position must
                appear before declaring a repetition draw (>=2). Value 4 matches
                official shogi rules.
        """
        self.progress_queue = progress_queue

        # Initialize time control (no implicit fallback defaults).
        self.time_control_limits = (
            coerce_time_control_limits(time_control_limits) if time_control_limits is not None else None
        )
        self.adjudication_config = adjudication_config
        if repetition_occurrences_to_draw < 2:
            raise ValueError("repetition_occurrences_to_draw must be at least 2")
        self.repetition_occurrences_to_draw = repetition_occurrences_to_draw

        self._is_shutting_down: bool = False
        self._engine_options_callback = on_engine_options
        self._published_engine_options: set[str] = set()
        self._apply_move_log_threshold_ms = 50.0
        self._clock_notify_log_threshold_ms = 50.0

    def set_engine_options_callback(
        self,
        callback: Callable[[str, Any, dict[str, str] | None], None] | None,
    ) -> None:
        """Install a callback invoked when USI option snapshots become available."""

        self._engine_options_callback = callback
        self._published_engine_options.clear()

    def request_shutdown(self) -> None:
        """Mark runner as shutting down to avoid further USI chatter."""
        self._is_shutting_down = True
