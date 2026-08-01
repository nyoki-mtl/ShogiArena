"""GameExecutionSpec の output contract を適用する。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
    GameExecutionSpec,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


def execution_provenance(
    spec: GameExecutionSpec,
    *,
    started_at: datetime,
    finished_at: datetime,
    engine_info: Mapping[str, Mapping[str, str]] | None = None,
) -> JsonObject:
    """Spec から秘密を含まない共通 provenance を構築する。"""

    def engine_payload(side: str) -> JsonObject:
        engine = spec.black_engine if side == "black" else spec.white_engine
        effective_options: JsonObject = dict(engine.usi.static_options)
        effective_options.update(engine.usi.variant_options)
        for resource in engine.usi.path_resources:
            effective_options.update(resource.option_values)
        return {
            "engine_id": engine.engine_id,
            "artifact_id": engine.process.artifact.logical_id,
            "artifact_sha256": engine.process.artifact.sha256,
            "effective_options": effective_options,
        }

    black = engine_payload("black")
    white = engine_payload("white")
    provenance: JsonObject = {
        "engine_artifacts": {
            "black": {
                "logical_id": black["artifact_id"],
                "sha256": black["artifact_sha256"],
            },
            "white": {
                "logical_id": white["artifact_id"],
                "sha256": white["artifact_sha256"],
            },
        },
        "effective_options": {
            "black": black["effective_options"],
            "white": white["effective_options"],
        },
        "timestamps": {
            "started_at": started_at.astimezone(UTC).isoformat(),
            "finished_at": finished_at.astimezone(UTC).isoformat(),
        },
    }
    if engine_info is not None:
        provenance["engine_identity"] = {
            side: {str(key): str(value) for key, value in values.items()} for side, values in engine_info.items()
        }
    return provenance


def build_game_execution_result(
    spec: GameExecutionSpec,
    *,
    classification: str,
    started_at: datetime,
    finished_at: datetime,
    engine_info: Mapping[str, Mapping[str, str]] | None = None,
) -> GameExecutionResult:
    """record/provenance 契約を検証して result envelope を返す。"""

    if spec.output.record_policy != "required":
        raise ValueError("current game execution adapters require output.record_policy='required'")
    provenance = execution_provenance(
        spec,
        started_at=started_at,
        finished_at=finished_at,
        engine_info=engine_info,
    )
    missing = sorted(set(spec.output.required_provenance) - set(provenance))
    if missing:
        raise ValueError(f"required output provenance is unavailable: {', '.join(missing)}")
    return GameExecutionResult(
        execution_digest=spec.execution_digest,
        game_id=spec.identity.game_id,
        classification=classification,
        provenance={name: provenance[name] for name in spec.output.required_provenance},
    )


__all__ = ["build_game_execution_result", "execution_provenance"]
