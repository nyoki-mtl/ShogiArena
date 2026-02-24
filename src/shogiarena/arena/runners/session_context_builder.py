"""SessionContext builder helpers for runners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias, cast

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.session import SessionContext, SessionMetadata
from shogiarena.arena.storage import RunStorage

SessionServices: TypeAlias = Mapping[str, object]


class SessionContextBuilder:
    """Build SessionContext instances with runner-specific metadata."""

    @staticmethod
    def for_spsa(
        *,
        storage: RunStorage,
        num_workers: int,
        instance_pool: InstancePool | None,
        run_id: str,
        experiment_name: str,
        dashboard_enabled: bool,
        no_resume: bool,
        session_uuid: str,
        services: SessionServices,
    ) -> SessionContext:
        metadata = {
            "runner_type": "spsa",
            "experiment_name": experiment_name,
            "dashboard_enabled": dashboard_enabled,
            "num_workers": int(num_workers),
            "no_resume": no_resume,
            "session_uuid": session_uuid,
        }
        metadata = cast(SessionMetadata, metadata)
        return SessionContext.build(
            storage=storage,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=metadata,
            services=services,
        )

    @staticmethod
    def for_tournament(
        *,
        storage: RunStorage,
        num_workers: int,
        instance_pool: InstancePool | None,
        run_id: str,
        experiment_name: str,
        scheduler: str,
        games_per_pair: int,
        num_engines: int,
        dashboard_enabled: bool,
        services: SessionServices,
    ) -> SessionContext:
        metadata = {
            "runner_type": "tournament",
            "experiment_name": experiment_name,
            "scheduler": scheduler,
            "games_per_pair": int(games_per_pair),
            "num_engines": int(num_engines),
            "dashboard_enabled": dashboard_enabled,
        }
        metadata = cast(SessionMetadata, metadata)
        return SessionContext.build(
            storage=storage,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=metadata,
            services=services,
        )
