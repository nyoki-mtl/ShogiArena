from __future__ import annotations

import re
from pathlib import Path

import yaml

from shogiarena._core.contexts.instances.application.instance_models import InstanceConfig, InstanceType
from shogiarena._core.shared.kernel.json_types import JsonObject


class InstanceConfigStore:
    """Persist ``InstanceConfig`` definitions under ``output_dir/instances``."""

    _SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return InstanceConfigStore._SAFE_NAME.sub("-", name.strip())

    def path_for(self, instance_name: str) -> Path:
        return self.base_dir / f"{self._sanitize_name(instance_name)}.yaml"

    def save(self, config: InstanceConfig) -> Path:
        """Write instance configuration to disk and return the path."""

        path = self.path_for(config.name)
        payload = self._payload_from_config(config)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
        return path

    def delete(self, instance_name: str) -> None:
        """Delete the YAML file backing the given instance when it exists."""

        path = self.path_for(instance_name)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    @staticmethod
    def _payload_from_config(config: InstanceConfig) -> JsonObject:
        payload: JsonObject = {
            "type": config.type.value,
            "name": config.name,
            "slots": config.slots,
        }
        if config.max_engines is not None:
            payload["max_engines"] = config.max_engines
        if config.tags:
            payload["tags"] = list(config.tags)

        if config.type is InstanceType.SSH:
            if config.host:
                payload["host"] = config.host
            if config.user:
                payload["user"] = config.user
            if config.port:
                payload["port"] = config.port
            if config.identity_file:
                payload["identity_file"] = config.identity_file
            if config.project_root:
                payload["project_root"] = config.project_root
            payload["is_strict_host_key_checking"] = bool(config.is_strict_host_key_checking)
            if config.should_install_requirements:
                payload["should_install_requirements"] = True
        else:
            if config.is_strict_host_key_checking:
                payload["is_strict_host_key_checking"] = bool(config.is_strict_host_key_checking)
            if config.should_install_requirements:
                payload["should_install_requirements"] = True

        return payload
