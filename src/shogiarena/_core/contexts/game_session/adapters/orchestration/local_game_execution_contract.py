"""Local一局実行をversioned GameExecutionSpecへ解決する。"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Literal

from rsshogi.core import normalize_usi_position

from shogiarena import __version__
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_spec_resolver import (
    EngineSpecResolveRequest,
    GameSpecResolveRequest,
    ResolvedGameExecution,
    resolve_game_execution,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    ExecutionIdentity,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    RepetitionSpec,
    TargetPlatform,
    TimeoutPolicySpec,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def resolve_local_game_execution(
    *,
    run_id: str,
    game_id: str,
    initial_sfen: str,
    black_name: str,
    white_name: str,
    black_config_path: Path,
    white_config_path: Path,
    black_artifact_overlay_options: JsonObject | None,
    white_artifact_overlay_options: JsonObject | None,
    black_arena_options: JsonObject | None,
    white_arena_options: JsonObject | None,
    black_overlay_options: JsonObject | None,
    white_overlay_options: JsonObject | None,
    black_inline_options: JsonObject | None,
    white_inline_options: JsonObject | None,
    black_variant_options: JsonObject | None,
    white_variant_options: JsonObject | None,
    black_variant_id: str | None,
    white_variant_id: str | None,
    clear_hash_before_game: bool,
    after_variant_setoption: str,
    black_path_option_names: tuple[str, ...],
    white_path_option_names: tuple[str, ...],
    black_go_options: JsonObject,
    white_go_options: JsonObject,
    black_handshake_timeout_s: float,
    white_handshake_timeout_s: float,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    rules: Any,
    engine_lifecycle: Literal["reuse", "per_game"],
    timeout_reclassification_enabled: bool,
    target_platform: TargetPlatform | None = None,
) -> ResolvedGameExecution:
    """Local engine acquisitionと同じresolve済み入力から一局contractを作る。"""

    target = target_platform or _local_platform()
    adjudication = rules.adjudication
    max_plies = adjudication.max_plies if adjudication.is_max_plies_enabled else None
    return resolve_game_execution(
        GameSpecResolveRequest(
            minimum_worker_version=__version__,
            identity=ExecutionIdentity(
                job_id=f"{run_id}:{game_id}",
                run_id=run_id,
                game_id=game_id,
            ),
            black_engine=EngineSpecResolveRequest(
                config_path=black_config_path,
                engine_id=black_name,
                artifact_logical_id=f"{black_name}-{target.operating_system}",
                target_platform=target,
                artifact_overlay_options=_scalar_options(
                    black_artifact_overlay_options,
                    field_name="black_artifact_overlay_options",
                ),
                arena_options=_scalar_options(black_arena_options, field_name="black_arena_options"),
                overlay_options=_scalar_options(
                    black_overlay_options,
                    field_name="black_overlay_options",
                ),
                inline_options=_scalar_options(
                    black_inline_options,
                    field_name="black_inline_options",
                ),
                variant_options=_scalar_options(
                    black_variant_options,
                    field_name="black_variant_options",
                ),
                go_options=_scalar_options(black_go_options, field_name="black_go_options"),
                path_option_names=black_path_option_names,
                variant_id=black_variant_id,
                lifecycle=engine_lifecycle,
                clear_hash_before_game=clear_hash_before_game,
                after_variant_setoption=_after_setoption(after_variant_setoption),
                default_handshake_timeout_s=black_handshake_timeout_s,
            ),
            white_engine=EngineSpecResolveRequest(
                config_path=white_config_path,
                engine_id=white_name,
                artifact_logical_id=f"{white_name}-{target.operating_system}",
                target_platform=target,
                artifact_overlay_options=_scalar_options(
                    white_artifact_overlay_options,
                    field_name="white_artifact_overlay_options",
                ),
                arena_options=_scalar_options(white_arena_options, field_name="white_arena_options"),
                overlay_options=_scalar_options(
                    white_overlay_options,
                    field_name="white_overlay_options",
                ),
                inline_options=_scalar_options(
                    white_inline_options,
                    field_name="white_inline_options",
                ),
                variant_options=_scalar_options(
                    white_variant_options,
                    field_name="white_variant_options",
                ),
                go_options=_scalar_options(white_go_options, field_name="white_go_options"),
                path_option_names=white_path_option_names,
                variant_id=white_variant_id,
                lifecycle=engine_lifecycle,
                clear_hash_before_game=clear_hash_before_game,
                after_variant_setoption=_after_setoption(after_variant_setoption),
                default_handshake_timeout_s=white_handshake_timeout_s,
            ),
            opening=OpeningSpec(
                initial_sfen=normalize_usi_position(initial_sfen),
                black_engine_id=black_name,
                white_engine_id=white_name,
            ),
            time=GameTimeSpec(
                black=_time_control(black_limits),
                white=_time_control(white_limits),
                startup_grace_ms=30_000,
                outer_deadline_ms=_outer_deadline_ms(black_limits, white_limits, max_plies=max_plies),
            ),
            rules=GameRulesSpec(
                adjudication=AdjudicationSpec(
                    resign_threshold_cp=adjudication.resign_threshold_cp,
                    resign_move_count=adjudication.resign_move_count,
                    resign_two_sided=adjudication.is_resign_two_sided,
                    max_plies=max_plies,
                    sync_max_plies_with_engine=adjudication.should_sync_max_plies_with_engine,
                ),
                repetition=RepetitionSpec(
                    occurrences_to_draw=rules.repetition_occurrences_to_draw,
                ),
            ),
            timeout=TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification=("invalid_on_coordinator_stall" if timeout_reclassification_enabled else "disabled"),
            ),
            required_tags=(),
            output=OutputContract(
                required_provenance=[
                    "engine_artifacts",
                    "engine_identity",
                    "effective_options",
                    "timestamps",
                ]
            ),
        )
    )


def _local_platform() -> TargetPlatform:
    operating_system: Literal["linux", "macos", "windows"]
    if sys.platform == "win32":
        operating_system = "windows"
    elif sys.platform.startswith("linux"):
        operating_system = "linux"
    elif sys.platform == "darwin":
        operating_system = "macos"
    else:
        raise RuntimeError(f"GameExecutionSpec does not support local platform: {sys.platform}")

    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        architecture: Literal["arm64", "x86_64"] = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        architecture = "arm64"
    else:
        raise RuntimeError(f"GameExecutionSpec does not support local architecture: {machine or 'unknown'}")
    return TargetPlatform(operating_system=operating_system, architecture=architecture)


def _time_control(limits: TimeControlLimits) -> GameTimeControlSpec:
    return GameTimeControlSpec.model_validate(limits.model_dump(mode="python"))


def _outer_deadline_ms(
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    *,
    max_plies: int | None,
) -> int:
    plies = max_plies or 320
    return 40_000 + plies * max(black_limits.max_wait_ms, white_limits.max_wait_ms)


def _scalar_options(options: JsonObject | None, *, field_name: str) -> dict[str, JsonScalar]:
    result: dict[str, JsonScalar] = {}
    for name, value in (options or {}).items():
        if value is None or isinstance(value, str | int | float | bool):
            result[str(name)] = value
            continue
        raise TypeError(f"{field_name}.{name} must be a scalar JSON value")
    return result


def _after_setoption(value: str) -> Literal["none", "isready"]:
    if value == "none":
        return "none"
    if value == "isready":
        return "isready"
    raise ValueError("after_variant_setoption must be one of: none, isready")


__all__ = ["resolve_local_game_execution"]
