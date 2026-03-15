"""Parsing logic for instance configuration YAML."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import OptionalText, coerce_int

from .instance_config_models import InstanceConfig, InstancesConfig, InstanceType


class _InstanceConfigInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: InstanceType = InstanceType.LOCAL
    project_root: OptionalText = None
    host: OptionalText = None
    user: OptionalText = None
    port: int = 22
    identity_file: OptionalText = None
    slots: int | None = None
    max_engines: int | None = None
    tags: list[str] = Field(default_factory=list)
    is_strict_host_key_checking: bool = True
    should_install_requirements: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, value: JsonValue | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("name is required")
        return normalized

    @field_validator("slots", "max_engines", mode="before")
    @classmethod
    def _coerce_optional_int(cls, value: JsonValue | None) -> int | None:
        if value in (None, ""):
            return None
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("must be an integer or null")
        return parsed

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: JsonValue | None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("tags must be a list")
        return [str(item) for item in value]


class _InstancesConfigInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instances: list[_InstanceConfigInput] = Field(default_factory=list)


def parse_instances_config(data: Mapping[str, JsonValue]) -> InstancesConfig:
    raw_instances = data.get("instances")
    if isinstance(raw_instances, list):
        for entry in raw_instances:
            if not isinstance(entry, Mapping):
                continue
            if "engine_dir" in entry:
                raise ValueError("engine_dir must not be specified; use project_root instead or omit")
    try:
        parsed = _InstancesConfigInput.model_validate(dict(data))
    except ValidationError as exc:
        raise ValueError(f"Invalid instances config: {exc}") from exc

    instances: list[InstanceConfig] = []
    for inst_data in parsed.instances:
        if inst_data.type == InstanceType.LOCAL:
            engine_dir = ""
            project_root = ""
        else:
            if inst_data.project_root:
                project_root = inst_data.project_root
            else:
                project_root = "$HOME/ShogiArena-remote"
            engine_dir = str(Path(project_root) / "data" / "engines")

        instance = InstanceConfig(
            name=inst_data.name,
            type=inst_data.type,
            engine_dir=engine_dir,
            project_root=project_root,
            host=inst_data.host,
            user=inst_data.user,
            port=inst_data.port,
            identity_file=inst_data.identity_file,
            slots=inst_data.slots,
            max_engines=inst_data.max_engines,
            tags=list(inst_data.tags),
            is_strict_host_key_checking=inst_data.is_strict_host_key_checking,
            should_install_requirements=inst_data.should_install_requirements,
        )
        instances.append(instance)
    return InstancesConfig(instances=instances)


__all__ = ["parse_instances_config"]
