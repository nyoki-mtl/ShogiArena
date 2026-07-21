from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.scalar_coercion.api import OptionalText, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize


class _RepoSettingsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    url: OptionalText = None
    build_config: OptionalText = None

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_required_path(cls, value: object | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


class _OpenBenchSettingsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server: OptionalText = None
    username: OptionalText = None
    password_env: OptionalText = "OPENBENCH_PASSWORD"


class _ArenaSettingsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output_dir: str | None = None
    engine_dir: str | None = None
    repos: dict[str, _RepoSettingsModel] = Field(default_factory=dict)
    github_token_env: str | None = None
    overlays: dict[str, str] = Field(default_factory=dict)
    openbench: _OpenBenchSettingsModel | None = None

    @field_validator("output_dir", "engine_dir", "github_token_env", mode="before")
    @classmethod
    def _coerce_optional_root_str(cls, value: object | None) -> str | None:
        return coerce_optional_text(value)

    @field_validator("overlays", mode="before")
    @classmethod
    def _coerce_overlays(cls, value: object | Mapping[str, object] | None) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("settings.overlays must be a mapping")
        overlays: dict[str, str] = {}
        for key, raw_path in value.items():
            normalized = str(raw_path or "").strip()
            if not normalized:
                continue
            overlays[str(key)] = normalized
        return overlays

    @field_validator("repos", mode="before")
    @classmethod
    def _coerce_repos(cls, value: object | Mapping[str, object] | None) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("settings.repos must be a mapping")
        return {str(key): json_serialize(item) for key, item in value.items()}


__all__ = [
    "_ArenaSettingsModel",
]
