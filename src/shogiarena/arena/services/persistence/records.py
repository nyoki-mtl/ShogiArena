"""Lightweight record structures used for persistence helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias, cast

JsonMapping: TypeAlias = Mapping[str, object]


@dataclass(slots=True)
class EngineArtifactSnapshot:
    """Materialized engine artifact metadata resolved at runtime."""

    logical_name: str
    artifact: str | None = None
    binary_path: str | None = None
    build_flags: JsonMapping | None = None
    metadata: JsonMapping | None = None


@dataclass(slots=True)
class InstanceSnapshot:
    """Instance characteristics captured when a job is executed."""

    instance_id: str
    display_name: str | None = None
    host_label: str | None = None
    cpu_model: str | None = None
    cpu_arch: str | None = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    memory_total_mb: int | None = None
    os_info: str | None = None
    gpu_model: str | None = None
    gpu_vendor: str | None = None
    gpu_vram_mb: int | None = None
    gpu_count: int | None = None
    instance_type: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    extra: JsonMapping | None = None


@dataclass(slots=True)
class GameParticipationRecord:
    """Describes how a single engine/instance participated in a game."""

    role: Literal["black", "white"]
    engine_name: str
    engine_display_name: str | None
    engine_artifact: EngineArtifactSnapshot | None
    instance: InstanceSnapshot | None
    binary_path: str | None
    build_flags: JsonMapping | None
    started_at: datetime | None
    completed_at: datetime | None
    run_id: str | None
    extra: JsonMapping | None = None


def serialize_participation_records(records: Sequence[GameParticipationRecord]) -> str:
    """Serialize participation records into metadata attribute JSON."""

    payload = [cast(dict[str, object], _encode_datetime_values(asdict(item))) for item in records]
    return json.dumps(payload, ensure_ascii=False)


def deserialize_participation_records(raw: str) -> tuple[GameParticipationRecord, ...]:
    """Decode participation records from metadata attribute JSON."""

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()

    records: list[GameParticipationRecord] = []
    for item in decoded:
        record = _decode_participation_item(item)
        if record is not None:
            records.append(record)
    return tuple(records)


def _decode_participation_item(value: object) -> GameParticipationRecord | None:
    if isinstance(value, GameParticipationRecord):
        return value
    d = _as_str_dict(value)
    if d is None:
        return None

    role_raw = _as_str(d.get("role"))
    engine_name = _as_str(d.get("engine_name"))
    if role_raw not in {"black", "white"} or engine_name is None:
        return None

    return GameParticipationRecord(
        role=cast(Literal["black", "white"], role_raw),
        engine_name=engine_name,
        engine_display_name=_as_str(d.get("engine_display_name")),
        engine_artifact=_decode_engine_artifact(d.get("engine_artifact")),
        instance=_decode_instance_snapshot(d.get("instance")),
        binary_path=_as_str(d.get("binary_path")),
        build_flags=_as_mapping(d.get("build_flags")),
        started_at=_as_datetime(d.get("started_at")),
        completed_at=_as_datetime(d.get("completed_at")),
        run_id=_as_str(d.get("run_id")),
        extra=_as_mapping(d.get("extra")),
    )


def _decode_engine_artifact(value: object) -> EngineArtifactSnapshot | None:
    if isinstance(value, EngineArtifactSnapshot):
        return value
    d = _as_str_dict(value)
    if d is None:
        return None
    logical_name = _as_str(d.get("logical_name"))
    if logical_name is None:
        return None
    return EngineArtifactSnapshot(
        logical_name=logical_name,
        artifact=_as_str(d.get("artifact")),
        binary_path=_as_str(d.get("binary_path")),
        build_flags=_as_mapping(d.get("build_flags")),
        metadata=_as_mapping(d.get("metadata")),
    )


def _decode_instance_snapshot(value: object) -> InstanceSnapshot | None:
    if isinstance(value, InstanceSnapshot):
        return value
    d = _as_str_dict(value)
    if d is None:
        return None
    instance_id = _as_str(d.get("instance_id"))
    if instance_id is None:
        return None
    tags_raw = d.get("tags")
    tags = tuple(str(item) for item in tags_raw) if isinstance(tags_raw, list | tuple) else ()
    return InstanceSnapshot(
        instance_id=instance_id,
        display_name=_as_str(d.get("display_name")),
        host_label=_as_str(d.get("host_label")),
        cpu_model=_as_str(d.get("cpu_model")),
        cpu_arch=_as_str(d.get("cpu_arch")),
        cpu_cores=_as_int(d.get("cpu_cores")),
        cpu_threads=_as_int(d.get("cpu_threads")),
        memory_total_mb=_as_int(d.get("memory_total_mb")),
        os_info=_as_str(d.get("os_info")),
        gpu_model=_as_str(d.get("gpu_model")),
        gpu_vendor=_as_str(d.get("gpu_vendor")),
        gpu_vram_mb=_as_int(d.get("gpu_vram_mb")),
        gpu_count=_as_int(d.get("gpu_count")),
        instance_type=_as_str(d.get("instance_type")),
        tags=tags,
        extra=_as_mapping(d.get("extra")),
    )


def _encode_datetime_values(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _encode_datetime_values(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode_datetime_values(item) for item in value]
    return value


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str | float):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _as_str_dict(value: object) -> dict[str, object] | None:
    """Safely narrow *value* to ``dict[str, object]`` (JSON object)."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _as_mapping(value: object) -> JsonMapping | None:
    d = _as_str_dict(value)
    if d is None:
        return None
    return {str(k): v for k, v in d.items()}
