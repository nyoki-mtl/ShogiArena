from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@dataclass(slots=True)
class EngineCatalogEntryRequest:
    name: str
    engine_config_path: Path | None = None
    artifact: str | None = None
    artifact_overlay_path: Path | None = None
    build_options: JsonObject = field(default_factory=dict)
    inline_options: JsonObject = field(default_factory=dict)
    options_overlay_paths: tuple[Path, ...] = ()
    time_control: TimeControlLimits | None = None
    instance_id: str | None = None


@dataclass(slots=True)
class EngineCatalogRequest:
    entries: tuple[EngineCatalogEntryRequest, ...]
    base_time_control: TimeControlLimits | None
    run_dir: Path
    path_output_dir: Path
    engine_dir: Path
    config_source_path: Path | str | None = None
    rules_synced_options: JsonObject = field(default_factory=dict)
    runtime_options: EngineOptionsSnapshots = field(default_factory=dict)
    runtime_info: EngineInfoSnapshots = field(default_factory=dict)


class EngineCatalogPort(Protocol):
    def build_metadata(self, request: EngineCatalogRequest) -> list[JsonObject]: ...
    def compute_time_control_specs(self, request: EngineCatalogRequest) -> tuple[dict[str, str], str | None]: ...
    def compute_instance_defaults(self, request: EngineCatalogRequest) -> dict[str, str | None]: ...


__all__ = [
    "EngineCatalogEntryRequest",
    "EngineCatalogPort",
    "EngineCatalogRequest",
]
