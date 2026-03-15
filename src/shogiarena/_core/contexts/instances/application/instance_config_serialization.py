"""Serialization of instance configuration to JSON-compatible dicts."""

from pydantic import BaseModel, ConfigDict, Field

from shogiarena._core.shared.kernel.json_types import JsonObject

from .instance_config_models import InstanceConfig, InstanceType


class _InstanceConfigOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    name: str
    type: InstanceType
    host: str | None = None
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    engine_dir: str
    project_root: str = ""
    slots: int | None = None
    max_engines: int | None = None
    tags: list[str] = Field(default_factory=list)
    is_strict_host_key_checking: bool = True
    should_install_requirements: bool = False


def serialize_instance_config(config: InstanceConfig) -> JsonObject:
    payload = _InstanceConfigOutput.model_validate(config, from_attributes=True)
    return payload.model_dump(mode="json")


__all__ = ["serialize_instance_config"]
