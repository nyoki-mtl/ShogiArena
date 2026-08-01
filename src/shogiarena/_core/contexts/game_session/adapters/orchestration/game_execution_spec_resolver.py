"""Engine YAMLをtransport-neutral GameExecutionSpecへ解決するadapter。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from shogiarena._core.contexts.game_session.application.preplaced_resource_mapping import (
    content_addressed_resource_logical_id,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    ExecutionIdentity,
    GameExecutionSpec,
    GameExecutionSpecPayload,
    GameRulesSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    PathResourceRef,
    PlatformArtifactRef,
    ResourceRequirements,
    TargetPlatform,
    TimeoutPolicySpec,
    seal_game_execution_spec,
)
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.shared.kernel.content_hashing import sha256_file_cached, sha256_path_cached
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.path_resources import BOOK_DISABLED_VALUES, PathResource, resolve_path_resources


@dataclass(frozen=True, slots=True)
class EngineSpecResolveRequest:
    """一方のengine contractを解決する入力。"""

    config_path: Path
    engine_id: str
    artifact_logical_id: str
    target_platform: TargetPlatform
    arena_options: Mapping[str, JsonScalar]
    variant_options: Mapping[str, JsonScalar]
    go_options: Mapping[str, JsonScalar]
    artifact_overlay_options: Mapping[str, JsonScalar] = field(default_factory=dict)
    overlay_options: Mapping[str, JsonScalar] = field(default_factory=dict)
    inline_options: Mapping[str, JsonScalar] = field(default_factory=dict)
    engine_source_override: Path | None = None
    path_option_names: tuple[str, ...] = ()
    variant_id: str | None = None
    lifecycle: Literal["reuse", "per_game"] = "reuse"
    clear_hash_before_game: bool = False
    after_variant_setoption: Literal["none", "isready"] = "none"
    default_handshake_timeout_s: float = 10.0


@dataclass(frozen=True, slots=True)
class EngineSpecProvenance:
    """Manifestへ保存するresolver input/output evidence。"""

    config_path: str
    engine_source_path: str
    engine_sha256: str
    path_sources: tuple[str, ...]
    path_digests: tuple[str, ...]

    def to_payload(self) -> JsonObject:
        """JSON manifest payloadへ変換する。"""

        return {
            "config_path": self.config_path,
            "engine_source_path": self.engine_source_path,
            "engine_sha256": self.engine_sha256,
            "path_sources": list(self.path_sources),
            "path_digests": list(self.path_digests),
        }


@dataclass(frozen=True, slots=True)
class ResolvedEngineExecution:
    """Engine specと同じ解決から得たprovenance。"""

    spec: EngineExecutionSpec
    provenance: EngineSpecProvenance


@dataclass(frozen=True, slots=True)
class GameSpecResolveRequest:
    """Local/Remoteに依存しない一局spec resolver入力。"""

    minimum_worker_version: str
    identity: ExecutionIdentity
    black_engine: EngineSpecResolveRequest
    white_engine: EngineSpecResolveRequest
    opening: OpeningSpec
    time: GameTimeSpec
    rules: GameRulesSpec
    timeout: TimeoutPolicySpec
    required_tags: tuple[str, ...]
    output: OutputContract


@dataclass(frozen=True, slots=True)
class ResolvedGameExecution:
    """封印済みspecとmanifest-ready provenance。"""

    spec: GameExecutionSpec
    engine_provenance: tuple[EngineSpecProvenance, EngineSpecProvenance]

    def manifest_payload(self) -> JsonObject:
        """Spec inputと物理provenanceを同じresolver結果から返す。"""

        return {
            "game_execution_spec": self.spec.model_dump(mode="json"),
            "engine_provenance": [item.to_payload() for item in self.engine_provenance],
        }


def resolve_game_execution(request: GameSpecResolveRequest) -> ResolvedGameExecution:
    """両engineを解決し、transport-neutralなGameExecutionSpecを封印する。"""

    black = resolve_engine_execution(request.black_engine)
    white = resolve_engine_execution(request.white_engine)
    artifact_ids = sorted(
        {
            black.spec.process.artifact.logical_id,
            white.spec.process.artifact.logical_id,
            *(resource.artifact.logical_id for resource in black.spec.usi.path_resources),
            *(resource.artifact.logical_id for resource in white.spec.usi.path_resources),
        }
    )
    payload = GameExecutionSpecPayload(
        minimum_worker_version=request.minimum_worker_version,
        identity=request.identity,
        black_engine=black.spec,
        white_engine=white.spec,
        opening=request.opening,
        time=request.time,
        rules=request.rules,
        timeout=request.timeout,
        resources=ResourceRequirements(
            required_tags=list(request.required_tags),
            engine_slots=2,
            artifact_ids=artifact_ids,
        ),
        output=request.output,
    )
    return ResolvedGameExecution(
        spec=seal_game_execution_spec(payload),
        engine_provenance=(black.provenance, white.provenance),
    )


def resolve_engine_execution(request: EngineSpecResolveRequest) -> ResolvedEngineExecution:
    """Engine YAMLを一度だけloadし、完全なengine execution contractへ解決する。"""

    config_path = request.config_path.resolve()
    config = UsiEngineConfig.from_file(config_path)
    engine_source = _require_engine_source(config, override=request.engine_source_override)
    if request.target_platform.operating_system == "linux" and engine_source.suffix.lower() == ".exe":
        raise ValueError(f"Windows engine binary cannot target Linux worker: {engine_source}")
    engine_digest = sha256_file_cached(engine_source)

    static_options: dict[str, JsonScalar] = _scalar_options(config.options, field_name="options")
    static_options.update(request.artifact_overlay_options)
    static_options.update(request.arena_options)
    static_options.update(request.overlay_options)
    static_options.update(request.inline_options)
    variant_options = dict(request.variant_options)
    for option_name in variant_options:
        static_options.pop(option_name, None)

    all_options = dict(static_options)
    all_options.update(variant_options)
    path_resources = resolve_path_resources(
        all_options,
        engine_dir=Path(config.working_directory) if config.working_directory else engine_source.parent,
        extra_scalar_keys=request.path_option_names,
    )
    resolved_resources = [
        _build_path_resource(
            resource,
            target_platform=request.target_platform,
            logical_prefix=request.artifact_logical_id,
        )
        for resource in path_resources
        if not _is_disabled_book(resource)
    ]
    for resource in path_resources:
        for option_name in resource.option_keys:
            if _is_disabled_book(resource) and option_name == "BookFile":
                continue
            static_options.pop(option_name, None)
            variant_options.pop(option_name, None)

    base_go_options = _scalar_options(config.go_options, field_name="go_options")
    base_go_options.update(request.go_options)
    runtime_config = config.with_overrides(
        options=all_options,
        go_options=base_go_options,
    ).resolve_isready_lock_key()
    engine_entrypoint = engine_source.name
    artifact = PlatformArtifactRef(
        logical_id=request.artifact_logical_id,
        kind="engine_binary",
        sha256=engine_digest,
        target_platform=request.target_platform,
        entrypoint=engine_entrypoint,
    )
    spec = EngineExecutionSpec(
        engine_id=request.engine_id,
        variant_id=request.variant_id,
        process=EngineProcessSpec(
            artifact=artifact,
            arguments=list(config.engine_args),
            working_directory=f"engines/{request.artifact_logical_id}",
            environment=dict(config.environment),
            secret_environment_refs=dict(config.secret_environment),
            lifecycle=request.lifecycle,
            handshake_timeout_ms=round(
                1000
                * (
                    config.handshake_timeout
                    if config.handshake_timeout is not None
                    else request.default_handshake_timeout_s
                )
            ),
            collect_info_strings=config.should_collect_info_strings,
            collect_raw_io=config.should_collect_raw_io,
            collect_stderr=config.should_collect_stderr,
            collect_outbound=config.should_collect_outbound,
        ),
        usi=EngineUsiSpec(
            static_options=static_options,
            variant_options=variant_options,
            go_options=base_go_options,
            path_resources=resolved_resources,
            option_validation=config.option_validation_default,
            option_validation_overrides=dict(config.option_validation_overrides),
            clear_hash_before_game=request.clear_hash_before_game,
            after_variant_setoption=request.after_variant_setoption,
            early_ponder=config.is_early_ponder_enabled,
            isready_sync_strategy=_sync_strategy(config.isready_sync_strategy),
            isready_lock_key=runtime_config.isready_lock_key,
            isready_lock_check_keys=list(runtime_config.isready_lock_check_templates),
            skip_isready_lock_if_exists=config.should_skip_isready_lock_if_exists,
            mate_default_ply_limit=config.mate_default_ply_limit,
            mate_default_node_limit=config.mate_default_node_limit,
            mate_default_infinite=config.is_mate_default_infinite,
            mate_wait_for_bestmove=config.should_mate_wait_for_bestmove,
        ),
    )
    return ResolvedEngineExecution(
        spec=spec,
        provenance=EngineSpecProvenance(
            config_path=str(config_path),
            engine_source_path=str(engine_source),
            engine_sha256=engine_digest,
            path_sources=tuple(
                resource.resolved_path for resource in path_resources if not _is_disabled_book(resource)
            ),
            path_digests=tuple(resource.artifact.sha256 for resource in resolved_resources),
        ),
    )


def _require_engine_source(config: UsiEngineConfig, *, override: Path | None) -> Path:
    engine_path = override if override is not None else config.engine_path
    if engine_path is None:
        raise ValueError("Resolved engine config must contain engine_path before GameExecutionSpec resolution")
    source = Path(engine_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Resolved engine binary not found: {source}")
    return source


def _scalar_options(options: Mapping[str, object], *, field_name: str) -> dict[str, JsonScalar]:
    result: dict[str, JsonScalar] = {}
    for key, value in options.items():
        if value is None or isinstance(value, str | int | float | bool):
            result[str(key)] = value
            continue
        raise TypeError(f"{field_name}.{key} must be a scalar JSON value")
    return result


def _build_path_resource(
    resource: PathResource,
    *,
    target_platform: TargetPlatform,
    logical_prefix: str,
) -> PathResourceRef:
    source = Path(resource.resolved_path).resolve()
    kind, digest = sha256_path_cached(source)
    artifact_kind: Literal["file", "directory"] = "file" if kind == "file" else "directory"
    logical_id = content_addressed_resource_logical_id(logical_prefix, digest)
    target_path = f"resources/sha256/{digest[:2]}/{digest}/{source.name}"
    option_values = _target_option_values(resource, target_path=target_path)
    return PathResourceRef(
        option_values=option_values,
        artifact=PlatformArtifactRef(
            logical_id=logical_id,
            kind=artifact_kind,
            sha256=digest,
            target_platform=target_platform,
        ),
        target_relative_path=target_path,
    )


def _target_option_values(resource: PathResource, *, target_path: str) -> dict[str, str]:
    if resource.kind == "scalar":
        return {resource.option_keys[0]: target_path}
    file_key = resource.option_keys[-1]
    directory_keys = resource.option_keys[:-1]
    values = {key: target_path.rsplit("/", 1)[0] for key in directory_keys}
    values[file_key] = target_path.rsplit("/", 1)[-1]
    return values


def _is_disabled_book(resource: PathResource) -> bool:
    return any(
        option_name == "BookFile" and value.strip().lower() in BOOK_DISABLED_VALUES
        for option_name, value in resource.original_values.items()
    )


def _sync_strategy(value: str) -> Literal["direct", "wait", "stop"]:
    if value == "direct":
        return "direct"
    if value == "wait":
        return "wait"
    if value == "stop":
        return "stop"
    raise ValueError(f"Unsupported isready_sync_strategy after config validation: {value}")


__all__ = [
    "EngineSpecProvenance",
    "EngineSpecResolveRequest",
    "GameSpecResolveRequest",
    "ResolvedEngineExecution",
    "ResolvedGameExecution",
    "resolve_engine_execution",
    "resolve_game_execution",
]
