from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shogiarena.arena.instances.pool import InstancePool


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Immutable runtime context shared between runner and orchestrator."""

    run_dir: Path
    num_workers: int
    run_id: str
    instance_pool: InstancePool | None = None
    metadata: Mapping[str, Any] | None = None
    services: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.num_workers < 1:
            raise ValueError("SessionContext.num_workers must be >= 1")
        if not self.run_dir.is_absolute():
            raise ValueError("SessionContext.run_dir must be an absolute path")
        if not self.run_dir.is_dir():
            raise ValueError(f"SessionContext.run_dir must exist: {self.run_dir}")
        if not self.run_id or not self.run_id.strip():
            raise ValueError("SessionContext.run_id must be a non-empty string")

    @classmethod
    def build(
        cls,
        *,
        run_dir: Path,
        num_workers: int,
        instance_pool: InstancePool | None = None,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        services: Mapping[str, Any] | None = None,
    ) -> SessionContext:
        """Construct a validated context from loosely-typed inputs."""

        resolved_dir = Path(run_dir).resolve(strict=False)
        if not resolved_dir.exists():
            raise ValueError(f"Session run_dir does not exist: {resolved_dir}")
        if not resolved_dir.is_dir():
            raise ValueError(f"Session run_dir must be a directory: {resolved_dir}")
        if num_workers < 1:
            raise ValueError(f"Session num_workers must be >= 1 (got {num_workers})")

        rid = (run_id or resolved_dir.name).strip()
        if not rid:
            raise ValueError("Session run_id cannot be empty")

        meta_copy = dict(metadata) if metadata is not None else None
        services_copy = dict(services) if services is not None else None
        return cls(
            run_dir=resolved_dir,
            num_workers=num_workers,
            run_id=rid,
            instance_pool=instance_pool,
            metadata=meta_copy,
            services=services_copy,
        )
