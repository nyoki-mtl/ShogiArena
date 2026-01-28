"""Lightweight record structures used for persistence helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(slots=True)
class EngineArtifactSnapshot:
    """Materialized engine artifact metadata resolved at runtime."""

    logical_name: str
    artifact: str | None = None
    binary_path: str | None = None
    build_flags: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(slots=True)
class InstanceSnapshot:
    """Instance characteristics captured when a job is executed."""

    instance_id: str
    display_name: str | None = None
    host_label: str | None = None
    cpu_model: str | None = None
    cpu_arch: str | None = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    memory_total_mb: int | None = None
    os_info: str | None = None
    gpu_model: str | None = None
    gpu_vendor: str | None = None
    gpu_vram_mb: int | None = None
    gpu_count: int | None = None
    instance_type: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    extra: Mapping[str, Any] | None = None


@dataclass(slots=True)
class GameParticipationRecord:
    """Describes how a single engine/instance participated in a game."""

    role: Literal["black", "white"]
    engine_name: str
    engine_display_name: str | None
    engine_artifact: EngineArtifactSnapshot | None
    instance: InstanceSnapshot | None
    binary_path: str | None
    build_flags: Mapping[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    run_id: str | None
    extra: Mapping[str, Any] | None = None
