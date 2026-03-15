"""Shared factory for building or resuming match session contexts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext


@dataclass(slots=True)
class SessionContextFactory:
    """Create session contexts with optional storage resume."""

    def build_or_resume(
        self,
        *,
        storage: RunStoragePort,
        instance_pool: Any | None,
        num_workers: int,
        run_id: str,
        metadata: Mapping[str, object],
        should_skip_resume: bool,
    ) -> SessionContext:
        if not should_skip_resume:
            loaded = SessionContext.load_from_storage(
                storage=storage,
                instance_pool=instance_pool,
            )
            if loaded is not None:
                return loaded

        return SessionContext.build(
            storage=storage,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=metadata,
        )


__all__ = ["SessionContextFactory"]
