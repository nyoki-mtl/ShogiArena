"""GameExecutionSpec の engine 契約を実行時 config へ materialize する。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from rsshogi.core import normalize_usi_position

from shogiarena._core.contexts.game_session.ports.game_execution_spec import EngineExecutionSpec, OpeningSpec
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.shared.kernel.json_types import JsonObject


class EngineOptionApplicationPort(Protocol):
    """一局固有 option を適用できる engine runtime。"""

    async def apply_engine_options(
        self,
        options: Mapping[str, object] | None,
        *,
        clear_hash: bool = True,
        after_setoption: str = "isready",
        validation: object | None = None,
    ) -> None: ...


def materialize_engine_config(
    spec: EngineExecutionSpec,
    *,
    execution_root: Path,
    artifact_paths: Mapping[str, Path] | None = None,
    secret_values: Mapping[str, str] | None = None,
) -> JsonObject:
    """封印済み engine spec と artifact locator から runtime config を作る。"""

    process = spec.process
    entrypoint = process.artifact.entrypoint
    if entrypoint is None:
        raise ValueError("engine artifact entrypoint is required")

    located_artifacts = artifact_paths or {}
    engine_path = located_artifacts.get(process.artifact.logical_id)
    working_directory = execution_root / Path(process.working_directory)
    if engine_path is None:
        engine_path = working_directory / entrypoint
    else:
        engine_path = engine_path.resolve()

    environment = dict(process.environment)
    for key, secret_ref in process.secret_environment_refs.items():
        value = (secret_values or {}).get(secret_ref)
        if value is None:
            raise ValueError(f"required engine secret reference is unavailable: {secret_ref}")
        environment[key] = value

    options: JsonObject = dict(spec.usi.static_options)
    for resource in spec.usi.path_resources:
        located = located_artifacts.get(resource.artifact.logical_id)
        target_path = located.resolve() if located is not None else execution_root / Path(resource.target_relative_path)
        target_text = resource.target_relative_path.replace("\\", "/")
        target_parent = target_text.rsplit("/", 1)[0] if "/" in target_text else "."
        target_name = target_text.rsplit("/", 1)[-1]
        for name, value in resource.option_values.items():
            normalized = value.replace("\\", "/")
            if normalized == target_text:
                options[name] = str(target_path)
            elif normalized == target_parent:
                options[name] = str(target_path.parent)
            elif normalized == target_name:
                options[name] = target_path.name
            else:
                raise ValueError(
                    f"path option {name!r} is not derived from target_relative_path {resource.target_relative_path!r}"
                )

    usi = spec.usi
    return {
        "name": spec.engine_id,
        "engine_path": str(engine_path),
        "working_directory": str(working_directory),
        "engine_args": list(process.arguments),
        "environment": environment,
        "options": options,
        "go_options": dict(usi.go_options),
        "enable_early_ponder": usi.early_ponder,
        "isready_sync_strategy": usi.isready_sync_strategy,
        "isready_lock_key": usi.isready_lock_key,
        "isready_lock_check_templates": list(usi.isready_lock_check_keys),
        "isready_lock_skip_if_exists": usi.skip_isready_lock_if_exists,
        "mate_default_ply_limit": usi.mate_default_ply_limit,
        "mate_default_node_limit": usi.mate_default_node_limit,
        "mate_default_infinite": usi.mate_default_infinite,
        "mate_wait_for_bestmove": usi.mate_wait_for_bestmove,
        "option_validation": {
            "default": usi.option_validation,
            "overrides": dict(usi.option_validation_overrides),
        },
        "io": {
            "collect_info_strings": process.collect_info_strings,
            "collect_raw_io": process.collect_raw_io,
            "collect_stderr": process.collect_stderr,
            "collect_outbound": process.collect_outbound,
        },
    }


def materialize_opening_sfen(opening: OpeningSpec) -> str:
    """開始局面へ moves_usi を適用した canonical SFEN を返す。"""

    if not opening.moves_usi:
        return normalize_usi_position(opening.initial_sfen)
    base = opening.initial_sfen.strip()
    if base == "startpos":
        position = "position startpos"
    elif base.startswith("position "):
        position = base
    else:
        position = f"position sfen {normalize_usi_position(base)}"
    return normalize_usi_position(f"{position} moves {' '.join(opening.moves_usi)}")


async def apply_engine_variant(engine: EngineOptionApplicationPort, spec: EngineExecutionSpec) -> None:
    """Spec の variant option policy を engine へ適用する。"""

    if not spec.usi.variant_options and not spec.usi.clear_hash_before_game:
        return
    validation: object = dict(spec.usi.option_validation_overrides) or spec.usi.option_validation
    await engine.apply_engine_options(
        spec.usi.variant_options,
        clear_hash=spec.usi.clear_hash_before_game,
        after_setoption=spec.usi.after_variant_setoption,
        validation=validation,
    )


def resolve_effective_handshake_timeout(
    configured_timeout: float | None,
    session_default: float | None,
) -> float:
    """Local/Remote共通のhandshake timeout precedenceを解決する。"""

    if configured_timeout is not None:
        return configured_timeout
    if session_default is not None:
        return session_default
    return AsyncUsiEngine.DEFAULT_HANDSHAKE_TIMEOUT


__all__ = [
    "EngineOptionApplicationPort",
    "apply_engine_variant",
    "materialize_engine_config",
    "materialize_opening_sfen",
    "resolve_effective_handshake_timeout",
]
