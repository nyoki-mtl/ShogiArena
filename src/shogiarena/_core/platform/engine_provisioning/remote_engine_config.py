"""Remote engine config normalization model."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.json_types import JsonScalar, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize


class RemoteEngineConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine_path: str | None = None
    artifact: str | None = None
    build_options: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator("engine_path", "artifact", mode="before")
    @classmethod
    def coerce_optional_path(cls, value: JsonValue | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @field_validator("build_options", mode="before")
    @classmethod
    def coerce_build_options(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> dict[str, JsonScalar]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("build_options must be a mapping")
        normalized: dict[str, JsonScalar] = {}
        for key, item in value.items():
            serialized = json_serialize(item)
            if isinstance(serialized, dict | list):
                raise TypeError("build_options values must be JSON scalar values")
            normalized[str(key)] = serialized
        return normalized


__all__ = ["RemoteEngineConfig"]
