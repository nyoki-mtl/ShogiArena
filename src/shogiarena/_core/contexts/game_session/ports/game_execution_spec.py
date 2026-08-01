"""Local/Remote 共通の一局実行契約。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.game_results import parse_game_result_name
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_json_bytes, canonical_sha256

GAME_EXECUTION_SPEC_SCHEMA_VERSION = "shogiarena.game-execution-spec.v1"
GAME_EXECUTION_PROTOCOL_VERSION = "shogiarena.remote-worker.v1"
GAME_RESULT_SCHEMA_VERSION = "shogiarena.game-result.v1"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SecretReference = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EngineOptionValue: TypeAlias = StrictStr | StrictInt | StrictFloat | StrictBool | None


class _StrictContractModel(BaseModel):
    """GameExecutionSpecを構成するfail-closed modelの基底。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionIdentity(_StrictContractModel):
    """一局をrun、game、SPSA pairへ関連付けるidentity。"""

    job_id: NonEmptyStr
    run_id: NonEmptyStr
    game_id: NonEmptyStr
    update_idx: StrictInt | None = Field(default=None, ge=0)
    pair_id: NonEmptyStr | None = None


class TargetPlatform(_StrictContractModel):
    """Artifactが実行対象とするplatform。"""

    operating_system: Literal["linux", "macos", "windows"]
    architecture: Literal["arm64", "x86_64"]


class PlatformArtifactRef(_StrictContractModel):
    """Content-addressed artifactの論理identity。"""

    logical_id: NonEmptyStr
    kind: Literal["engine_binary", "file", "directory"]
    sha256: Sha256Digest
    target_platform: TargetPlatform
    entrypoint: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _require_engine_entrypoint(self) -> Self:
        if self.kind == "engine_binary" and self.entrypoint is None:
            raise ValueError("engine_binary artifact requires entrypoint")
        if self.kind != "engine_binary" and self.entrypoint is not None:
            raise ValueError("entrypoint is only valid for engine_binary artifacts")
        return self


class PathResourceRef(_StrictContractModel):
    """USI path optionへ割り当てるartifact resource。"""

    option_values: dict[NonEmptyStr, StrictStr]
    artifact: PlatformArtifactRef
    target_relative_path: NonEmptyStr

    @model_validator(mode="after")
    def _validate_resource(self) -> Self:
        if not self.option_values:
            raise ValueError("path resource must define at least one option value")
        if self.artifact.kind == "engine_binary":
            raise ValueError("path resource artifact must be a file or directory")
        return self


class EngineProcessSpec(_StrictContractModel):
    """Engine child processへ渡すtransport-neutralなprocess設定。"""

    artifact: PlatformArtifactRef
    arguments: list[StrictStr] = Field(default_factory=list)
    working_directory: NonEmptyStr
    environment: dict[NonEmptyStr, StrictStr] = Field(default_factory=dict)
    secret_environment_refs: dict[NonEmptyStr, SecretReference] = Field(default_factory=dict)
    lifecycle: Literal["reuse", "per_game"] = "reuse"
    handshake_timeout_ms: StrictInt = Field(gt=0)
    collect_info_strings: StrictBool = False
    collect_raw_io: StrictBool = True
    collect_stderr: StrictBool = True
    collect_outbound: StrictBool = True

    @model_validator(mode="after")
    def _require_engine_artifact(self) -> Self:
        if self.artifact.kind != "engine_binary":
            raise ValueError("engine process artifact must be an engine_binary")
        overlap = set(self.environment) & set(self.secret_environment_refs)
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(f"environment and secret_environment_refs keys overlap: {joined}")
        return self


class EngineUsiSpec(_StrictContractModel):
    """Resolve済みUSI optionとSPSA variant適用policy。"""

    static_options: dict[NonEmptyStr, EngineOptionValue] = Field(default_factory=dict)
    variant_options: dict[NonEmptyStr, EngineOptionValue] = Field(default_factory=dict)
    go_options: dict[NonEmptyStr, EngineOptionValue] = Field(default_factory=dict)
    path_resources: list[PathResourceRef] = Field(default_factory=list)
    option_validation: Literal["strict", "warn", "raw", "allow_unlisted_combo_value"] = "strict"
    option_validation_overrides: dict[
        NonEmptyStr,
        Literal["strict", "warn", "raw", "allow_unlisted_combo_value"],
    ] = Field(default_factory=dict)
    clear_hash_before_game: StrictBool = False
    after_variant_setoption: Literal["none", "isready"] = "none"
    early_ponder: StrictBool = False
    isready_sync_strategy: Literal["direct", "wait", "stop"] = "direct"
    isready_lock_key: NonEmptyStr | None = None
    isready_lock_check_keys: list[NonEmptyStr] = Field(default_factory=list)
    skip_isready_lock_if_exists: StrictBool = False
    mate_default_ply_limit: StrictInt | None = Field(default=None, gt=0)
    mate_default_node_limit: StrictInt | None = Field(default=None, gt=0)
    mate_default_infinite: StrictBool = False
    mate_wait_for_bestmove: StrictBool = False

    @model_validator(mode="after")
    def _require_unique_path_options(self) -> Self:
        option_names = [option_name for resource in self.path_resources for option_name in resource.option_values]
        if len(option_names) != len(set(option_names)):
            raise ValueError("path resource option names must be unique")
        overlap = (set(self.static_options) & set(self.variant_options)) | (
            set(option_names) & (set(self.static_options) | set(self.variant_options))
        )
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(f"static, variant, and path option groups overlap: {joined}")
        if self.mate_default_ply_limit is not None and self.mate_default_node_limit is not None:
            raise ValueError("mate default ply and node limits cannot be combined")
        if self.mate_default_infinite and (
            self.mate_default_ply_limit is not None or self.mate_default_node_limit is not None
        ):
            raise ValueError("mate_default_infinite cannot be combined with mate limits")
        return self


class EngineExecutionSpec(_StrictContractModel):
    """一方の参加engineに必要な完全な実行設定。"""

    engine_id: NonEmptyStr
    variant_id: NonEmptyStr | None = None
    process: EngineProcessSpec
    usi: EngineUsiSpec


class OpeningSpec(_StrictContractModel):
    """開始局面、開始手順、先後assignment。"""

    initial_sfen: NonEmptyStr
    moves_usi: list[NonEmptyStr] = Field(default_factory=list)
    black_engine_id: NonEmptyStr
    white_engine_id: NonEmptyStr


class GameTimeControlSpec(_StrictContractModel):
    """Wire上で完結する一方分の持ち時間設定。"""

    time_ms: StrictInt | None = Field(default=None, ge=0)
    increment_ms: StrictInt | None = Field(default=None, ge=0)
    byoyomi_ms: StrictInt | None = Field(default=None, ge=0)
    fixed_time_ms: StrictInt | None = Field(default=None, gt=0)
    depth_limit: StrictInt | None = Field(default=None, gt=0)
    node_limit: StrictInt | None = Field(default=None, gt=0)
    expiry_margin_ms: StrictInt = Field(default=500, ge=0)
    should_allow_timeout: StrictBool = False
    max_wait_ms: StrictInt = Field(default=600_000, gt=0)

    @model_validator(mode="after")
    def _validate_mode(self) -> Self:
        has_main_time = self.time_ms is not None
        has_increment = self.increment_ms is not None and self.increment_ms > 0
        has_byoyomi = self.byoyomi_ms is not None and self.byoyomi_ms > 0
        has_fixed = self.fixed_time_ms is not None
        has_search_limit = self.depth_limit is not None or self.node_limit is not None
        if not (has_main_time or has_fixed or has_search_limit):
            raise ValueError("time control must specify time_ms, fixed_time_ms, depth_limit, or node_limit")
        if has_fixed and (has_main_time or has_increment or has_byoyomi):
            raise ValueError("fixed_time_ms cannot be combined with time_ms, increment_ms, or byoyomi_ms")
        if has_increment and has_byoyomi:
            raise ValueError("increment_ms and byoyomi_ms cannot be combined")
        if (has_increment or has_byoyomi) and not has_main_time:
            raise ValueError("time_ms is required with increment_ms or byoyomi_ms")
        return self


class GameTimeSpec(_StrictContractModel):
    """先後のclockと外側deadline計算に必要な時間policy。"""

    black: GameTimeControlSpec
    white: GameTimeControlSpec
    startup_grace_ms: StrictInt = Field(ge=0)
    outer_deadline_ms: StrictInt = Field(gt=0)


class AdjudicationSpec(_StrictContractModel):
    """投了および最大手数policy。"""

    resign_threshold_cp: StrictInt | None = Field(default=None, gt=0)
    resign_move_count: StrictInt = Field(default=8, gt=0)
    resign_two_sided: StrictBool = True
    max_plies: StrictInt | None = Field(default=320, gt=0)
    sync_max_plies_with_engine: StrictBool = True


class RepetitionSpec(_StrictContractModel):
    """千日手判定policy。"""

    occurrences_to_draw: Literal[2, 3, 4] = 2


class GameRulesSpec(_StrictContractModel):
    """一局の判定rule。"""

    adjudication: AdjudicationSpec
    repetition: RepetitionSpec


class TimeoutPolicySpec(_StrictContractModel):
    """Timeoutのwatchdog証拠と帰属・再分類policy。"""

    watchdog: Literal["required", "disabled"]
    origin_attribution: Literal["required", "best_effort"]
    reclassification: Literal["invalid_on_coordinator_stall", "disabled"]


class ResourceRequirements(_StrictContractModel):
    """Schedulerがdispatch前に満たすresource条件。"""

    required_tags: list[NonEmptyStr] = Field(default_factory=list)
    engine_slots: Literal[2] = 2
    artifact_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_unique_values(self) -> Self:
        if len(self.required_tags) != len(set(self.required_tags)):
            raise ValueError("required_tags values must be unique")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("artifact_ids values must be unique")
        return self


class OutputContract(_StrictContractModel):
    """Workerが返すresult、provenance、recordの契約。"""

    result_schema: Literal["shogiarena.game-result.v1"] = GAME_RESULT_SCHEMA_VERSION
    required_provenance: list[
        Literal[
            "endpoint",
            "deployment",
            "engine_artifacts",
            "engine_identity",
            "effective_options",
            "timestamps",
        ]
    ] = Field(default_factory=list)
    record_policy: Literal["required", "optional", "disabled"] = "required"

    @model_validator(mode="after")
    def _require_unique_provenance(self) -> Self:
        if len(self.required_provenance) != len(set(self.required_provenance)):
            raise ValueError("required_provenance values must be unique")
        return self


class GameExecutionSpecPayload(_StrictContractModel):
    """Digestを付与する前の完全なGameExecutionSpec payload。"""

    schema_version: Literal["shogiarena.game-execution-spec.v1"] = GAME_EXECUTION_SPEC_SCHEMA_VERSION
    protocol_version: Literal["shogiarena.remote-worker.v1"] = GAME_EXECUTION_PROTOCOL_VERSION
    minimum_worker_version: NonEmptyStr
    identity: ExecutionIdentity
    black_engine: EngineExecutionSpec
    white_engine: EngineExecutionSpec
    opening: OpeningSpec
    time: GameTimeSpec
    rules: GameRulesSpec
    timeout: TimeoutPolicySpec
    resources: ResourceRequirements
    output: OutputContract

    @model_validator(mode="after")
    def _validate_cross_references(self) -> Self:
        if self.black_engine.engine_id != self.opening.black_engine_id:
            raise ValueError("opening.black_engine_id does not match black_engine.engine_id")
        if self.white_engine.engine_id != self.opening.white_engine_id:
            raise ValueError("opening.white_engine_id does not match white_engine.engine_id")
        declared_artifacts = {
            self.black_engine.process.artifact.logical_id,
            self.white_engine.process.artifact.logical_id,
        }
        for engine in (self.black_engine, self.white_engine):
            declared_artifacts.update(resource.artifact.logical_id for resource in engine.usi.path_resources)
        if set(self.resources.artifact_ids) != declared_artifacts:
            raise ValueError("resources.artifact_ids must exactly match artifacts referenced by both engines")
        return self


class GameExecutionSpec(GameExecutionSpecPayload):
    """Canonical digestで封印されたLocal/Remote共通の実行契約。"""

    execution_digest: Sha256Digest

    @model_validator(mode="after")
    def _validate_execution_digest(self) -> Self:
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"execution_digest"}))
        if self.execution_digest != expected:
            raise ValueError("execution_digest does not match the canonical GameExecutionSpec payload")
        return self

    def canonical_json_bytes(self) -> bytes:
        """Digestを含むcanonical JSON bytesを返す。"""

        return canonical_json_bytes(self.model_dump(mode="json"))


class GameExecutionResult(_StrictContractModel):
    """Local/Remoteで共通の一局result classification envelope。"""

    schema_version: Literal["shogiarena.game-result.v1"] = GAME_RESULT_SCHEMA_VERSION
    execution_digest: Sha256Digest
    game_id: NonEmptyStr
    classification: NonEmptyStr
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_classification(self) -> Self:
        parse_game_result_name(self.classification)
        return self


def seal_game_execution_spec(payload: GameExecutionSpecPayload) -> GameExecutionSpec:
    """Resolve済みpayloadへcanonical execution digestを付与する。"""

    serialized = payload.model_dump(mode="json")
    return GameExecutionSpec.model_validate(
        {
            **serialized,
            "execution_digest": canonical_sha256(serialized),
        }
    )


def parse_game_execution_spec(payload: Mapping[str, object]) -> GameExecutionSpec:
    """外部payloadをfail-closedで検証する。"""

    return parse_wire(
        boundary_id="BND-GAME-EXECUTION-SPEC-V1",
        payload=payload,
        model=GameExecutionSpec,
        path="game_execution_spec",
    )


__all__ = [
    "GAME_EXECUTION_PROTOCOL_VERSION",
    "GAME_EXECUTION_SPEC_SCHEMA_VERSION",
    "GAME_RESULT_SCHEMA_VERSION",
    "AdjudicationSpec",
    "EngineExecutionSpec",
    "EngineProcessSpec",
    "EngineUsiSpec",
    "ExecutionIdentity",
    "GameExecutionSpec",
    "GameExecutionSpecPayload",
    "GameExecutionResult",
    "GameRulesSpec",
    "GameTimeControlSpec",
    "GameTimeSpec",
    "OpeningSpec",
    "OutputContract",
    "PathResourceRef",
    "PlatformArtifactRef",
    "RepetitionSpec",
    "ResourceRequirements",
    "TargetPlatform",
    "TimeoutPolicySpec",
    "parse_game_execution_spec",
    "seal_game_execution_spec",
]
