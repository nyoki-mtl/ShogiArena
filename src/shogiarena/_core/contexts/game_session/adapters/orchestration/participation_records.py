"""Participation snapshot builders extracted from BaseOrchestrator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    GameExecutionSpec,
)
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.shared.kernel.book_provenance import build_book_provenance
from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object_or_none,
    coerce_json_object_serialized,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.participation_records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
    serialize_participation_records,
)

from .config_engine import EngineConfig


class _ParticipantPort(Protocol):
    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def name(self) -> str: ...


def collect_participation_records_local(
    orchestrator: Any,
    *,
    black_engine: AsyncUsiEngine,
    white_engine: AsyncUsiEngine,
    black_participant: _ParticipantPort,
    white_participant: _ParticipantPort,
    black_spec: EngineConfig | None,
    white_spec: EngineConfig | None,
    black_pool_key: str,
    white_pool_key: str,
    started_at: datetime,
    completed_at: datetime,
) -> list[GameParticipationRecord]:
    records: list[GameParticipationRecord] = []
    black_config = black_engine.config
    white_config = white_engine.config
    records.append(
        _construct_participation_record(
            orchestrator,
            role="black",
            engine_name=black_spec.name
            if black_spec is not None and black_spec.name is not None
            else black_engine.name,
            display_name=black_participant.name,
            spec=black_spec,
            engine_config=black_config,
            binary_path=black_config.engine_path,
            instance_id=(black_spec.instance_id if black_spec is not None else None) or "local",
            started_at=started_at,
            completed_at=completed_at,
            pool_key=black_pool_key,
            engine_options=dict(black_config.options),
            go_options=dict(black_config.go_options),
            environment=dict(black_config.environment),
            engine_info=dict(black_engine.engine_info),
            execution_metadata={"fixed_option_evidence_scope": "local_source"},
        )
    )
    records.append(
        _construct_participation_record(
            orchestrator,
            role="white",
            engine_name=white_spec.name
            if white_spec is not None and white_spec.name is not None
            else white_engine.name,
            display_name=white_participant.name,
            spec=white_spec,
            engine_config=white_config,
            binary_path=white_config.engine_path,
            instance_id=(white_spec.instance_id if white_spec is not None else None) or "local",
            started_at=started_at,
            completed_at=completed_at,
            pool_key=white_pool_key,
            engine_options=dict(white_config.options),
            go_options=dict(white_config.go_options),
            environment=dict(white_config.environment),
            engine_info=dict(white_engine.engine_info),
            execution_metadata={"fixed_option_evidence_scope": "local_source"},
        )
    )
    return records


def collect_participation_records_remote_pair(
    orchestrator: Any,
    *,
    spec_payload: Mapping[str, object] | None,
    black_engine_name: str,
    white_engine_name: str,
    black_spec: EngineConfig | None,
    white_spec: EngineConfig | None,
    black_pool_key: str,
    white_pool_key: str,
    instance_id: str | None,
    started_at: datetime,
    completed_at: datetime,
    remote_execution: Mapping[str, object] | None = None,
    worker_provenance: Mapping[str, object] | None = None,
) -> list[GameParticipationRecord]:
    if spec_payload is None:
        raise ValueError("remote participation requires a sealed GameExecutionSpec")
    sealed = GameExecutionSpec.model_validate(spec_payload)
    engine_identity = (
        coerce_json_object_or_none(worker_provenance.get("engine_identity")) if worker_provenance is not None else None
    )
    remote_execution_payload: JsonObject = coerce_json_object_serialized(
        remote_execution or {},
        field_name="remote_execution",
    )
    if worker_provenance is not None:
        worker_timestamps = coerce_json_object_or_none(worker_provenance.get("timestamps"))
        if worker_timestamps is not None:
            remote_execution_payload["worker_execution_timestamps"] = worker_timestamps

    return [
        _remote_participation_record(
            orchestrator=orchestrator,
            role="black",
            engine_name=black_engine_name,
            config_spec=black_spec,
            execution_spec=sealed.black_engine,
            pool_key=black_pool_key,
            instance_id=instance_id,
            started_at=started_at,
            completed_at=completed_at,
            engine_identity=engine_identity,
            remote_execution=remote_execution_payload,
        ),
        _remote_participation_record(
            orchestrator=orchestrator,
            role="white",
            engine_name=white_engine_name,
            config_spec=white_spec,
            execution_spec=sealed.white_engine,
            pool_key=white_pool_key,
            instance_id=instance_id,
            started_at=started_at,
            completed_at=completed_at,
            engine_identity=engine_identity,
            remote_execution=remote_execution_payload,
        ),
    ]


def _remote_participation_record(
    *,
    orchestrator: Any,
    role: Literal["black", "white"],
    engine_name: str,
    config_spec: EngineConfig | None,
    execution_spec: EngineExecutionSpec,
    pool_key: str,
    instance_id: str | None,
    started_at: datetime,
    completed_at: datetime,
    engine_identity: Mapping[str, object] | None,
    remote_execution: Mapping[str, object] | None,
) -> GameParticipationRecord:
    process = execution_spec.process
    artifact = process.artifact
    entrypoint = artifact.entrypoint
    binary_path = f"{process.working_directory}/{entrypoint}" if entrypoint is not None else None
    effective_options: JsonObject = dict(execution_spec.usi.static_options)
    effective_options.update(execution_spec.usi.variant_options)
    for resource in execution_spec.usi.path_resources:
        effective_options.update(resource.option_values)
    reported = coerce_json_object_or_none(engine_identity.get(role)) if engine_identity is not None else None
    artifact_snapshot = EngineArtifactSnapshot(
        logical_name=artifact.logical_id,
        artifact=artifact.logical_id,
        binary_path=binary_path,
        metadata={
            "sha256": artifact.sha256,
            "kind": artifact.kind,
            "target_platform": artifact.target_platform.model_dump(mode="json"),
        },
    )
    execution_metadata: JsonObject = {
        "fixed_option_evidence_scope": "remote_runtime",
        "fixed_option_file_set": {
            "scope": "remote_runtime",
            "status": "inventory_only",
            "source": "sealed_game_execution_spec_artifact_inventory",
            "engine_artifact": {
                "logical_id": artifact.logical_id,
                "sha256": artifact.sha256,
                "entrypoint": artifact.entrypoint,
            },
            "path_resources": [
                {
                    "logical_id": resource.artifact.logical_id,
                    "sha256": resource.artifact.sha256,
                    "target_relative_path": resource.target_relative_path,
                }
                for resource in execution_spec.usi.path_resources
            ],
        },
        "resolved_engine_arguments": list(process.arguments),
        "non_secret_environment_keys": sorted(process.environment),
        "remote_execution": coerce_json_object_serialized(
            remote_execution or {},
            field_name="remote_execution",
        ),
    }
    return _construct_participation_record(
        orchestrator,
        role=role,
        engine_name=engine_name,
        display_name=engine_name,
        spec=config_spec,
        engine_config=None,
        binary_path=binary_path,
        instance_id=instance_id,
        started_at=started_at,
        completed_at=completed_at,
        pool_key=pool_key,
        engine_options=effective_options,
        go_options=execution_spec.usi.go_options,
        environment=None,
        engine_info=reported,
        artifact_snapshot=artifact_snapshot,
        execution_metadata=execution_metadata,
    )


def attach_participation_metadata(
    *,
    game_record: Any,
    participation_records: Sequence[GameParticipationRecord],
) -> None:
    if not participation_records:
        return
    encoded = serialize_participation_records(list(participation_records))
    game_record.set_metadata_attribute("_arena_participation", encoded)


def _engine_artifact_snapshot(
    *,
    engine_name: str,
    spec: EngineConfig | None,
    engine_config: UsiEngineConfig | None,
    binary_path: str | None,
) -> EngineArtifactSnapshot:
    artifact: str | None = None
    build_flags: JsonObject = {}
    metadata: JsonObject = {}

    if spec is not None:
        artifact = spec.artifact or artifact
        build_flags.update({str(key): value for key, value in spec.build_options.items()})
        cfg_path = spec.engine_path
        if cfg_path is not None:
            metadata["engine_path"] = str(cfg_path)
        overlays = spec.options_overlays
        if overlays:
            metadata["options_overlays"] = [str(p) for p in overlays]
        inst_id = spec.instance_id
        if inst_id:
            metadata["requested_instance_id"] = str(inst_id)

    if engine_config is not None:
        cfg_artifact = engine_config.artifact
        if cfg_artifact:
            artifact = cfg_artifact
        build_flags.update({str(key): value for key, value in engine_config.build_options.items()})
        exec_path = engine_config.engine_path
        if exec_path and not binary_path:
            binary_path = exec_path
        working_dir = engine_config.working_directory
        if working_dir:
            metadata["working_directory"] = str(working_dir)

    metadata = {k: v for k, v in metadata.items() if v is not None}
    build_flags_payload: JsonObject | None = build_flags if build_flags else None
    metadata_payload: JsonObject | None = metadata if metadata else None
    return EngineArtifactSnapshot(
        logical_name=engine_name,
        artifact=artifact,
        binary_path=binary_path,
        build_flags=build_flags_payload,
        metadata=metadata_payload,
    )


def _instance_snapshot(orchestrator: Any, instance_id: str | None) -> InstanceSnapshot | None:
    if not instance_id:
        return None
    pool = orchestrator.instance_pool
    if pool is None:
        return InstanceSnapshot(instance_id=instance_id)
    inst = pool.get_instance(instance_id)
    if inst is None:
        if instance_id == "local":
            inst = pool.ensure_local_instance()
        else:
            return InstanceSnapshot(instance_id=instance_id)
    metrics = inst.metrics
    tags = tuple(inst.config.tags or [])
    extra: JsonObject = {
        "slots": inst.config.slots,
        "reachable": metrics.is_reachable,
    }
    if inst.source_path is not None:
        extra["config_path"] = str(inst.source_path)
    if inst.is_draining:
        extra["draining"] = True
    return InstanceSnapshot(
        instance_id=inst.name,
        display_name=inst.name,
        host_label=inst.config.host,
        cpu_model=metrics.cpu_model,
        cpu_arch=None,
        cpu_cores=metrics.cpu_count,
        cpu_threads=metrics.cpu_count,
        memory_total_mb=metrics.mem_total_mb,
        os_info=None,
        gpu_model=None,
        gpu_vendor=None,
        gpu_vram_mb=None,
        gpu_count=None,
        instance_type=inst.type.value,
        tags=tags,
        extra=extra,
    )


def _construct_participation_record(
    orchestrator: Any,
    *,
    role: Literal["black", "white"],
    engine_name: str,
    display_name: str | None,
    spec: EngineConfig | None,
    engine_config: UsiEngineConfig | None,
    binary_path: str | None,
    instance_id: str | None,
    started_at: datetime,
    completed_at: datetime,
    pool_key: str | None,
    engine_options: Mapping[str, object] | None,
    go_options: Mapping[str, object] | None,
    environment: Mapping[str, object] | None,
    engine_info: Mapping[str, object] | None,
    artifact_snapshot: EngineArtifactSnapshot | None = None,
    execution_metadata: Mapping[str, object] | None = None,
) -> GameParticipationRecord:
    if artifact_snapshot is None:
        artifact_snapshot = _engine_artifact_snapshot(
            engine_name=engine_name,
            spec=spec,
            engine_config=engine_config,
            binary_path=binary_path,
        )
    inst_snapshot = _instance_snapshot(orchestrator, instance_id)

    build_flags: JsonObject = {}
    if spec is not None:
        build_flags.update({str(key): value for key, value in spec.build_options.items()})
    if engine_config is not None:
        build_flags.update({str(key): value for key, value in engine_config.build_options.items()})

    extras: JsonObject = {
        "per_instance_bytes_hash": {
            "schema_version": 1,
            "status": "not_collected",
            "hash_source": "not_collected",
        }
    }
    if engine_options:
        serialized_options = coerce_json_object_serialized(
            engine_options,
            field_name="engine_options",
        )
        extras["engine_options"] = serialized_options
        # per-participation の book provenance（fingerprint は Book タブ集計の集計キー）。Task 0015。
        book_provenance = build_book_provenance(serialized_options)
        if book_provenance is not None:
            extras["book"] = book_provenance
    if go_options:
        extras["go_options"] = coerce_json_object_serialized(
            go_options,
            field_name="go_options",
        )
    if environment:
        extras["environment"] = coerce_json_object_serialized(
            environment,
            field_name="environment",
        )
    if engine_info:
        extras["engine_info"] = coerce_json_object_serialized(
            engine_info,
            field_name="engine_info",
        )
    if pool_key:
        extras["pool_key"] = pool_key
    if execution_metadata:
        extras.update(
            coerce_json_object_serialized(
                execution_metadata,
                field_name="execution_metadata",
            )
        )
    extras = {k: v for k, v in extras.items() if v}

    return GameParticipationRecord(
        role=role,
        engine_name=engine_name,
        engine_display_name=display_name,
        engine_artifact=artifact_snapshot,
        instance=inst_snapshot,
        binary_path=binary_path,
        build_flags=build_flags if build_flags else None,
        started_at=started_at,
        completed_at=completed_at,
        run_id=orchestrator.session_context.run_id,
        extra=extras,
    )


__all__ = [
    "attach_participation_metadata",
    "collect_participation_records_local",
    "collect_participation_records_remote_pair",
]
