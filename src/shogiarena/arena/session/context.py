from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypedDict, cast

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.storage import RunStorage

if TYPE_CHECKING:
    from shogiarena.arena.services.persistence.db_service import ArenaDBService
    from shogiarena.arena.services.statistics.rating_service import EloRatingService
    from shogiarena.arena.services.statistics.sprt import Sprt


class BaseSessionMetadata(TypedDict, total=False):
    runner_type: Literal["tournament", "spsa"]
    experiment_name: str
    dashboard_enabled: bool
    num_workers: int


class TournamentSessionMetadata(BaseSessionMetadata, total=False):
    scheduler: str
    games_per_pair: int
    num_engines: int


class SpsaSessionMetadata(BaseSessionMetadata, total=False):
    no_resume: bool
    session_uuid: str


SessionMetadata: TypeAlias = TournamentSessionMetadata | SpsaSessionMetadata


class TournamentServices(TypedDict, total=False):
    db: ArenaDBService
    rating: EloRatingService
    sprt: Sprt


class SpsaServices(TypedDict, total=False):
    db: ArenaDBService


SessionServices: TypeAlias = TournamentServices | SpsaServices


class SessionSnapshot(TypedDict):
    run_id: str
    num_workers: int
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Immutable runtime context shared between runner and orchestrator.

    run_dir は storage.run_dir を通じてアクセスする。
    """

    storage: RunStorage
    num_workers: int
    run_id: str
    instance_pool: InstancePool | None = None
    metadata: SessionMetadata | None = None
    services: SessionServices | None = None

    def __post_init__(self) -> None:
        if self.num_workers < 1:
            raise ValueError("SessionContext.num_workers must be >= 1")
        if not self.storage.run_dir.is_absolute():
            raise ValueError("SessionContext.storage.run_dir must be an absolute path")
        if not self.storage.run_dir.is_dir():
            raise ValueError(f"SessionContext.storage.run_dir must exist: {self.storage.run_dir}")
        if not self.run_id or not self.run_id.strip():
            raise ValueError("SessionContext.run_id must be a non-empty string")

    @classmethod
    def build(
        cls,
        *,
        storage: RunStorage,
        num_workers: int,
        instance_pool: InstancePool | None = None,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        services: Mapping[str, Any] | None = None,
    ) -> SessionContext:
        """Construct a validated context from loosely-typed inputs."""

        run_dir = storage.run_dir
        if not run_dir.exists():
            raise ValueError(f"Session run_dir does not exist: {run_dir}")
        if not run_dir.is_dir():
            raise ValueError(f"Session run_dir must be a directory: {run_dir}")
        if num_workers < 1:
            raise ValueError(f"Session num_workers must be >= 1 (got {num_workers})")

        rid = (run_id or run_dir.name).strip()
        if not rid:
            raise ValueError("Session run_id cannot be empty")

        meta_copy = cast(SessionMetadata, dict(metadata)) if metadata is not None else None
        services_copy = cast(SessionServices, dict(services)) if services is not None else None
        return cls(
            storage=storage,
            num_workers=num_workers,
            run_id=rid,
            instance_pool=instance_pool,
            metadata=meta_copy,
            services=services_copy,
        )

    def to_snapshot(self) -> SessionSnapshot:
        meta = dict(self.metadata) if self.metadata is not None else None
        return {
            "run_id": self.run_id,
            "num_workers": int(self.num_workers),
            "metadata": meta,
        }

    def save_to_storage(self, *, filename: str = "session_context.json") -> None:
        self.storage.write_json(filename, self.to_snapshot())

    @classmethod
    def from_snapshot(
        cls,
        *,
        storage: RunStorage,
        snapshot: Mapping[str, Any],
        instance_pool: InstancePool | None = None,
        services: Mapping[str, Any] | None = None,
    ) -> SessionContext:
        run_id = snapshot.get("run_id")
        num_workers = snapshot.get("num_workers")
        metadata = snapshot.get("metadata")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Session snapshot run_id must be a non-empty string")
        if not isinstance(num_workers, int) or num_workers < 1:
            raise ValueError("Session snapshot num_workers must be >= 1")
        meta = dict(metadata) if isinstance(metadata, Mapping) else None
        return cls.build(
            storage=storage,
            num_workers=num_workers,
            instance_pool=instance_pool,
            run_id=run_id,
            metadata=meta,
            services=services,
        )

    @classmethod
    def load_from_storage(
        cls,
        *,
        storage: RunStorage,
        instance_pool: InstancePool | None = None,
        services: Mapping[str, Any] | None = None,
        filename: str = "session_context.json",
    ) -> SessionContext | None:
        payload = storage.read_json(filename)
        if payload is None:
            return None
        return cls.from_snapshot(storage=storage, snapshot=payload, instance_pool=instance_pool, services=services)
