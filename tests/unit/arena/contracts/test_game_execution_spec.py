"""Versioned GameExecutionSpec wire contract tests (task 0054 Phase 2.1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import ValidationError

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    ExecutionIdentity,
    GameExecutionSpec,
    GameExecutionSpecPayload,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    PathResourceRef,
    PlatformArtifactRef,
    RepetitionSpec,
    ResourceRequirements,
    TargetPlatform,
    TimeoutPolicySpec,
    parse_game_execution_spec,
    seal_game_execution_spec,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError

_ENGINE_A_DIGEST = "a" * 64
_ENGINE_B_DIGEST = "b" * 64
_MODEL_DIGEST = "c" * 64


def _artifact(
    logical_id: str,
    digest: str,
    *,
    kind: str = "engine_binary",
    entrypoint: str | None = "engine",
) -> PlatformArtifactRef:
    return PlatformArtifactRef.model_validate(
        {
            "logical_id": logical_id,
            "kind": kind,
            "sha256": digest,
            "target_platform": {
                "operating_system": "linux",
                "architecture": "x86_64",
            },
            "entrypoint": entrypoint,
        }
    )


def _engine(
    engine_id: str,
    artifact: PlatformArtifactRef,
    *,
    options: Mapping[str, object],
    variant_id: str | None = None,
    path_resource: PathResourceRef | None = None,
) -> EngineExecutionSpec:
    return EngineExecutionSpec(
        engine_id=engine_id,
        variant_id=variant_id,
        process=EngineProcessSpec(
            artifact=artifact,
            arguments=["--usi"],
            working_directory=".",
            environment={"OMP_NUM_THREADS": "1"},
            lifecycle="reuse",
            handshake_timeout_ms=10_000,
        ),
        usi=EngineUsiSpec.model_validate(
            {
                "static_options": dict(options),
                "variant_options": {"ParamA": 12} if variant_id is not None else {},
                "go_options": {"nodes": 1000},
                "path_resources": [] if path_resource is None else [path_resource],
                "option_validation": "strict",
                "clear_hash_before_game": variant_id is not None,
                "after_variant_setoption": "isready" if variant_id is not None else "none",
            }
        ),
    )


def _payload(*, static_option_order: tuple[str, str] = ("Threads", "Hash")) -> GameExecutionSpecPayload:
    platform = TargetPlatform(operating_system="linux", architecture="x86_64")
    model_artifact = PlatformArtifactRef(
        logical_id="model-a",
        kind="file",
        sha256=_MODEL_DIGEST,
        target_platform=platform,
    )
    model_resource = PathResourceRef(
        option_values={"EvalDir": "resources", "EvalFile": "model.bin"},
        artifact=model_artifact,
        target_relative_path="resources/model.bin",
    )
    option_values: dict[str, object] = {"Threads": 1, "Hash": 128}
    ordered_options = {name: option_values[name] for name in static_option_order}
    black = _engine(
        "engine-a",
        _artifact("engine-a-linux", _ENGINE_A_DIGEST),
        options=ordered_options,
        variant_id="plus",
        path_resource=model_resource,
    )
    white = _engine(
        "engine-b",
        _artifact("engine-b-linux", _ENGINE_B_DIGEST),
        options={"Threads": 1, "Hash": 128},
        variant_id="minus",
    )
    time_control = GameTimeControlSpec(time_ms=10_000, byoyomi_ms=1_000)
    return GameExecutionSpecPayload(
        minimum_worker_version="1.1.0",
        identity=ExecutionIdentity(
            job_id="job-0001",
            run_id="run-0001",
            game_id="game-0001",
            update_idx=3,
            pair_id="pair-0003-0001",
        ),
        black_engine=black,
        white_engine=white,
        opening=OpeningSpec(
            initial_sfen="lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
            moves_usi=["7g7f", "3c3d"],
            black_engine_id="engine-a",
            white_engine_id="engine-b",
        ),
        time=GameTimeSpec(
            black=time_control,
            white=time_control,
            startup_grace_ms=30_000,
            outer_deadline_ms=900_000,
        ),
        rules=GameRulesSpec(
            adjudication=AdjudicationSpec(
                resign_threshold_cp=2000,
                resign_move_count=8,
                resign_two_sided=True,
                max_plies=320,
                sync_max_plies_with_engine=True,
            ),
            repetition=RepetitionSpec(occurrences_to_draw=4),
        ),
        timeout=TimeoutPolicySpec(
            watchdog="required",
            origin_attribution="required",
            reclassification="invalid_on_coordinator_stall",
        ),
        resources=ResourceRequirements(
            required_tags=["cpu"],
            engine_slots=2,
            artifact_ids=["engine-a-linux", "engine-b-linux", "model-a"],
        ),
        output=OutputContract(
            required_provenance=[
                "endpoint",
                "deployment",
                "engine_artifacts",
                "effective_options",
                "timestamps",
            ],
            record_policy="required",
        ),
    )


def test_seal_and_parse_round_trip_complete_contract() -> None:
    spec = seal_game_execution_spec(_payload())

    parsed = parse_game_execution_spec(spec.model_dump(mode="json"))

    assert parsed == spec
    assert parsed.identity.update_idx == 3
    assert parsed.black_engine.usi.path_resources[0].artifact.logical_id == "model-a"
    assert parsed.output.result_schema == "shogiarena.game-result.v1"


def test_canonical_digest_is_stable_for_mapping_insertion_order() -> None:
    left = seal_game_execution_spec(_payload(static_option_order=("Threads", "Hash")))
    right = seal_game_execution_spec(_payload(static_option_order=("Hash", "Threads")))

    assert left.execution_digest == right.execution_digest
    assert left.canonical_json_bytes() == right.canonical_json_bytes()


def test_stale_execution_digest_is_rejected() -> None:
    spec = seal_game_execution_spec(_payload())
    mutated = spec.model_dump(mode="json")
    mutated["identity"]["game_id"] = "game-0002"  # type: ignore[index]

    with pytest.raises(ContractParseError) as exc_info:
        parse_game_execution_spec(mutated)

    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert "execution_digest" in str(exc_info.value.__cause__)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "shogiarena.game-execution-spec.v2"),
        (("protocol_version",), "shogiarena.remote-worker.v2"),
        (("black_engine", "process", "artifact", "target_platform", "operating_system"), "macos"),
    ],
)
def test_unknown_contract_or_platform_version_fails_closed(path: tuple[str, ...], value: str) -> None:
    raw = seal_game_execution_spec(_payload()).model_dump(mode="json")
    target: dict[str, object] = raw
    for segment in path[:-1]:
        target = cast(dict[str, object], target[segment])
    target[path[-1]] = value

    with pytest.raises(ContractParseError):
        parse_game_execution_spec(raw)


def test_unknown_top_level_and_nested_fields_are_rejected() -> None:
    raw = seal_game_execution_spec(_payload()).model_dump(mode="json")
    raw["unknown"] = True
    raw["black_engine"]["usi"]["legacy_option"] = "ignored"  # type: ignore[index]

    with pytest.raises(ContractParseError) as exc_info:
        parse_game_execution_spec(raw)

    assert isinstance(exc_info.value.__cause__, ValidationError)
    errors = exc_info.value.__cause__.errors()
    assert {tuple(error["loc"]) for error in errors} >= {
        ("unknown",),
        ("black_engine", "usi", "legacy_option"),
    }


def test_resource_manifest_must_exactly_cover_referenced_artifacts() -> None:
    raw = _payload().model_dump(mode="json")
    raw["resources"]["artifact_ids"] = ["engine-a-linux", "engine-b-linux"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="must exactly match"):
        GameExecutionSpecPayload.model_validate(raw)


def test_engine_environment_accepts_only_non_secret_string_values() -> None:
    raw = _payload().model_dump(mode="json")
    raw["black_engine"]["process"]["environment"]["THREADS"] = 2  # type: ignore[index]

    with pytest.raises(ValidationError):
        GameExecutionSpecPayload.model_validate(raw)


def test_secret_environment_contains_only_reference_and_is_part_of_digest() -> None:
    raw = _payload().model_dump(mode="json")
    secret_value = "must-never-enter-game-spec"
    raw["black_engine"]["process"]["secret_environment_refs"] = {  # type: ignore[index]
        "ENGINE_TOKEN": "SHOGIARENA_SECRET_ENGINE_TOKEN"
    }

    sealed = seal_game_execution_spec(GameExecutionSpecPayload.model_validate(raw))
    canonical = sealed.canonical_json_bytes()

    assert b"SHOGIARENA_SECRET_ENGINE_TOKEN" in canonical
    assert secret_value.encode() not in canonical


def test_secret_environment_rejects_literal_like_reference() -> None:
    raw = _payload().model_dump(mode="json")
    raw["black_engine"]["process"]["secret_environment_refs"] = {  # type: ignore[index]
        "ENGINE_TOKEN": "literal-secret-value"
    }

    with pytest.raises(ValidationError, match="secret_environment_refs"):
        GameExecutionSpecPayload.model_validate(raw)


def test_secret_and_non_secret_environment_keys_cannot_overlap() -> None:
    raw = _payload().model_dump(mode="json")
    raw["black_engine"]["process"]["environment"]["ENGINE_TOKEN"] = "plain"  # type: ignore[index]
    raw["black_engine"]["process"]["secret_environment_refs"] = {  # type: ignore[index]
        "ENGINE_TOKEN": "SHOGIARENA_SECRET_ENGINE_TOKEN"
    }

    with pytest.raises(ValidationError, match="keys overlap"):
        GameExecutionSpecPayload.model_validate(raw)


def test_invalid_time_control_is_rejected_at_contract_boundary() -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        GameTimeControlSpec(time_ms=10_000, increment_ms=100, byoyomi_ms=100)


def test_engine_artifact_requires_entrypoint() -> None:
    with pytest.raises(ValidationError, match="requires entrypoint"):
        _artifact("engine", _ENGINE_A_DIGEST, entrypoint=None)


def test_unsealed_payload_cannot_parse_as_execution_spec() -> None:
    with pytest.raises(ValidationError):
        GameExecutionSpec.model_validate(_payload().model_dump(mode="json"))
