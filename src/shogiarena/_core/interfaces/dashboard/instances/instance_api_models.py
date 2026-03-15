"""Pydantic models for instances API handlers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.contexts.instances.application.instance_config_models import InstanceType
from shogiarena._core.interfaces.dashboard.api_query_models import QueryValue, SummaryStreamQuery


class _SseInstancesQuery(SummaryStreamQuery):
    poll_interval: float = 3.0
    should_send_initial: bool = Field(default=True, alias="send_initial")


class _InstanceConfigPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    type: InstanceType | None = None
    slots: int | str | None = None
    tags: str | list[str] | None = None
    is_strict_host_key_checking: bool | None = None
    host: str | None = None
    user: str | None = None
    port: int | str | None = None
    identity_file: str | None = None
    project_root: str | None = None
    max_engines: int | str | None = None
    should_install_requirements: bool | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _validate_tags(cls, value: QueryValue | list[str]) -> str | list[str] | None:
        if value is None or isinstance(value, str):
            return value
        if not isinstance(value, list):
            raise ValueError("tags must be a list of strings")
        for item in value:
            if not isinstance(item, str):
                raise ValueError("tags must contain only strings")
        return [item for item in value if isinstance(item, str)]

    @field_validator("host", "user", "identity_file", "project_root", "name", mode="before")
    @classmethod
    def _validate_optional_string_fields(cls, value: QueryValue) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("must be a string")
        return value


class _InstanceActionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str
    local_path: str | None = None
    remote_path: str | None = None
    mode: Literal["dir", "file"] | None = None
    is_executable: bool | None = Field(default=None, alias="executable")


__all__ = [
    "_InstanceActionPayload",
    "_InstanceConfigPayload",
    "_SseInstancesQuery",
]
