from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from shogiarena._core.contexts.engine_catalog.ports.file_loader import EngineCatalogFileLoaderPort


class YamlFileLoaderAdapter(EngineCatalogFileLoaderPort):
    def load_yaml_mapping(self, path: Path) -> Mapping[str, object]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise TypeError(f"YAML payload must be a mapping: {path}")
        return raw


__all__ = ["YamlFileLoaderAdapter"]
