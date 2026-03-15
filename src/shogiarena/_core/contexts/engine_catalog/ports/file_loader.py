from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class EngineCatalogFileLoaderPort(Protocol):
    def load_yaml_mapping(self, path: Path) -> Mapping[str, object]: ...


__all__ = ["EngineCatalogFileLoaderPort"]
