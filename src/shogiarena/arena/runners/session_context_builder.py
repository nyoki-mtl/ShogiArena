"""SessionContext builder helpers for runners."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.session import SessionContext


class SessionContextBuilder:
    """Build SessionContext instances with runner-specific metadata."""

    @staticmethod
    def for_spsa(
        *,
        run_dir: Path,
        num_workers: int,
        instance_pool: InstancePool | None,
        run_id: str,
        experiment_name: str,
        dashboard_enabled: bool,
        overwrite: bool,
        session_uuid: str,
        services: Mapping[str, Any],
    ) -> SessionContext:
        metadata = {
            "runner_type": "spsa",
            "experiment_name": experiment_name,
            "dashboard_enabled": dashboard_enabled,
            "num_workers": int(num_workers),
            "overwrite": overwrite,
            "session_uuid": session_uuid,
        }
        return SessionContext.build(
            run_dir=run_dir,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=metadata,
            services=services,
        )

    @staticmethod
    def for_tournament(
        *,
        run_dir: Path,
        num_workers: int,
        instance_pool: InstancePool | None,
        run_id: str,
        experiment_name: str,
        scheduler: str,
        games_per_pair: int,
        num_engines: int,
        dashboard_enabled: bool,
        services: Mapping[str, Any],
    ) -> SessionContext:
        metadata = {
            "runner_type": "tournament",
            "experiment_name": experiment_name,
            "scheduler": scheduler,
            "games_per_pair": int(games_per_pair),
            "num_engines": int(num_engines),
            "dashboard_enabled": dashboard_enabled,
        }
        return SessionContext.build(
            run_dir=run_dir,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=metadata,
            services=services,
        )
