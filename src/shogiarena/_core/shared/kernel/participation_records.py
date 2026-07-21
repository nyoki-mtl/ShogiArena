from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, field_validator

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_iso_datetime, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)


def _to_mapping_or_none(value: JsonValue | Mapping[str, JsonValue] | None) -> JsonObject | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): json_serialize(item) for key, item in value.items()}


_CoercedOptionalStr = Annotated[str | None, BeforeValidator(coerce_str)]
# NOTE: ここを JsonObject にすると pydantic が再帰型 JsonValue のスキーマ生成で
# RecursionError になる（JsonValue は暗黙の再帰エイリアス）。境界では object のままにする。
_CoercedOptionalMapping = Annotated[Mapping[str, object] | None, BeforeValidator(_to_mapping_or_none)]


class EngineArtifactSnapshot(BaseModel):
    """Materialized engine artifact metadata resolved at runtime."""

    logical_name: str
    artifact: _CoercedOptionalStr = None
    binary_path: _CoercedOptionalStr = None
    build_flags: _CoercedOptionalMapping = None
    metadata: _CoercedOptionalMapping = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("logical_name", mode="before")
    @classmethod
    def _validate_logical_name(cls, value: JsonValue | None) -> str:
        normalized = coerce_str(value)
        if normalized is None:
            raise ValueError("logical_name is required")
        return normalized


class InstanceSnapshot(BaseModel):
    """Instance characteristics captured when a job is executed."""

    instance_id: str
    display_name: _CoercedOptionalStr = None
    host_label: _CoercedOptionalStr = None
    cpu_model: _CoercedOptionalStr = None
    cpu_arch: _CoercedOptionalStr = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    memory_total_mb: int | None = None
    os_info: _CoercedOptionalStr = None
    gpu_model: _CoercedOptionalStr = None
    gpu_vendor: _CoercedOptionalStr = None
    gpu_vram_mb: int | None = None
    gpu_count: int | None = None
    instance_type: _CoercedOptionalStr = None
    tags: tuple[str, ...] = ()
    extra: _CoercedOptionalMapping = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("instance_id", mode="before")
    @classmethod
    def _validate_instance_id(cls, value: JsonValue | None) -> str:
        normalized = coerce_str(value)
        if normalized is None:
            raise ValueError("instance_id is required")
        return normalized

    @field_validator(
        "cpu_cores",
        "cpu_threads",
        "memory_total_mb",
        "gpu_vram_mb",
        "gpu_count",
        mode="before",
    )
    @classmethod
    def _coerce_optional_int_fields(cls, value: JsonValue | None) -> int | None:
        return coerce_int(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: JsonValue | None) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple | list):
            return tuple(str(item) for item in value)
        return ()


class GameParticipationRecord(BaseModel):
    """Describes how a single engine/instance participated in a game."""

    role: Literal["black", "white"]
    engine_name: str
    engine_display_name: _CoercedOptionalStr = None
    engine_artifact: EngineArtifactSnapshot | None = None
    instance: InstanceSnapshot | None = None
    binary_path: _CoercedOptionalStr = None
    build_flags: _CoercedOptionalMapping = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    run_id: _CoercedOptionalStr = None
    extra: _CoercedOptionalMapping = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("engine_name", mode="before")
    @classmethod
    def _validate_engine_name(cls, value: JsonValue | None) -> str:
        normalized = coerce_str(value)
        if normalized is None:
            raise ValueError("engine_name is required")
        return normalized

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, value: JsonValue | datetime | None) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return coerce_iso_datetime(value)


def serialize_participation_records(records: Sequence[GameParticipationRecord]) -> str:
    """Serialize participation records into metadata attribute JSON."""

    payload = [record.model_dump(mode="json") for record in records]
    return json.dumps(payload, ensure_ascii=False)


def deserialize_participation_records(raw: str) -> tuple[GameParticipationRecord, ...]:
    """Decode participation records from metadata attribute JSON."""

    try:
        decoded = json.loads(raw)
    except JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()

    records: list[GameParticipationRecord] = []
    for item in decoded:
        try:
            records.append(GameParticipationRecord.model_validate(item))
        except ValidationError as exc:
            logger.debug("Skipping invalid participation record entry: %s", exc)
            continue
    return tuple(records)


def extract_participation(record: object) -> tuple[GameParticipationRecord, ...]:
    """Extract serialized participation records from a game record."""

    attrs = getattr(record, "metadata", None)
    attributes = getattr(attrs, "attributes", None) if attrs is not None else None
    if not isinstance(attributes, Mapping):
        return ()
    raw = attributes.get("_arena_participation")
    if not isinstance(raw, str) or not raw.strip():
        return ()
    return deserialize_participation_records(raw)


__all__ = [
    "EngineArtifactSnapshot",
    "GameParticipationRecord",
    "InstanceSnapshot",
    "deserialize_participation_records",
    "extract_participation",
    "serialize_participation_records",
]
