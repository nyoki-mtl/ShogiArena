"""GameExecutionSpecからLocal/Remote共通のGameRunner policyを構築する。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionSpec
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.domain.adjudication import AdjudicationConfig
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagProbePort
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@dataclass(frozen=True, slots=True)
class GameRunnerExecutionPolicy:
    """Runnerと一局実行時に渡すside別clock policy。"""

    runner: GameRunner
    black_limits: TimeControlLimits
    white_limits: TimeControlLimits


def build_game_runner_execution_policy(
    spec: GameExecutionSpec,
    *,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None = None,
    runtime_watchdog: LoopLagProbePort | None = None,
) -> GameRunnerExecutionPolicy:
    """Specのrules/time/timeoutを一つのGameRunner policyへ変換する。"""

    if (
        spec.timeout.watchdog == "required" or spec.timeout.origin_attribution == "required"
    ) and runtime_watchdog is None:
        raise ValueError("GameExecutionSpec requires a runtime watchdog for timeout origin attribution")

    black_limits = TimeControlLimits.model_validate(spec.time.black.model_dump(mode="python"))
    white_limits = TimeControlLimits.model_validate(spec.time.white.model_dump(mode="python"))
    adjudication = spec.rules.adjudication
    adjudication_config = AdjudicationConfig(
        is_resign_enabled=adjudication.resign_threshold_cp is not None,
        resign_score_cp=adjudication.resign_threshold_cp or 0,
        resign_move_count=adjudication.resign_move_count,
        is_resign_two_sided=adjudication.resign_two_sided,
        is_max_plies_enabled=adjudication.max_plies is not None,
        max_plies=adjudication.max_plies or 0,
    )
    runner = GameRunner(
        progress_queue=progress_queue,
        time_control_limits=black_limits,
        adjudication_config=adjudication_config,
        repetition_occurrences_to_draw=spec.rules.repetition.occurrences_to_draw,
    )
    runner.set_timeout_reclassification(spec.timeout.reclassification == "invalid_on_coordinator_stall")
    runner.set_runtime_watchdog(runtime_watchdog)
    return GameRunnerExecutionPolicy(
        runner=runner,
        black_limits=black_limits,
        white_limits=white_limits,
    )


__all__ = ["GameRunnerExecutionPolicy", "build_game_runner_execution_policy"]
