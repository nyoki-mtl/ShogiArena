"""Shared session context primitives for runtime/orchestration layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from typing_extensions import TypedDict

from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize

SessionMetadata: TypeAlias = Mapping[str, object]


class SessionSnapshot(TypedDict):
    run_id: str
    num_workers: int
    metadata: dict[str, object] | None


def _normalize_object_mapping(value: Mapping[Any, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, entry in value.items():
        if isinstance(key, str):
            normalized[key] = entry
    return normalized


def parse_session_snapshot(payload: Mapping[str, object], *, path: str = "root") -> SessionSnapshot:
    """Parse and validate session snapshot payload."""
    run_id = coerce_optional_text(payload.get("run_id"))
    if run_id is None:
        raise ValueError(f"{path}.run_id must be a non-empty string")

    num_workers_raw = payload.get("num_workers")
    num_workers = coerce_int(num_workers_raw)
    if num_workers is None or num_workers < 1:
        raise ValueError(f"{path}.num_workers must be a positive integer")

    metadata_raw = payload.get("metadata")
    metadata: dict[str, object] | None
    if metadata_raw is None:
        metadata = None
    elif isinstance(metadata_raw, Mapping):
        metadata = _normalize_object_mapping(metadata_raw)
    else:
        raise ValueError(f"{path}.metadata must be an object when provided")

    return {
        "run_id": run_id,
        "num_workers": num_workers,
        "metadata": metadata,
    }


def _normalize_session_metadata(metadata: SessionMetadata | None) -> dict[str, object] | None:
    if metadata is None:
        return None
    return _normalize_object_mapping(metadata)


def _serialize_session_snapshot(snapshot: SessionSnapshot) -> dict[str, object]:
    return {str(key): json_serialize(value) for key, value in snapshot.items()}


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Immutable runtime context shared between runner and orchestrator.

    runtime service は保持しない。resume 可能な metadata のみを管理する。
    """

    storage: RunStoragePort
    num_workers: int
    run_id: str
    instance_pool: Any | None = None
    metadata: dict[str, object] | None = None

    @property
    def run_dir(self) -> Path:
        return self.storage.run_dir

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
        storage: RunStoragePort,
        num_workers: int,
        instance_pool: Any | None = None,
        run_id: str | None = None,
        metadata: SessionMetadata | None = None,
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

        meta_copy = _normalize_session_metadata(metadata)
        return cls(
            storage=storage,
            num_workers=num_workers,
            run_id=rid,
            instance_pool=instance_pool,
            metadata=meta_copy,
        )

    def to_snapshot(self) -> SessionSnapshot:
        return {
            "run_id": self.run_id,
            "num_workers": self.num_workers,
            "metadata": self.metadata,
        }

    def save_to_storage(self, *, filename: str = "session_context.json") -> None:
        self.storage.write_json(filename, _serialize_session_snapshot(self.to_snapshot()))

    @classmethod
    def from_snapshot(
        cls,
        *,
        storage: RunStoragePort,
        snapshot: Mapping[str, object],
        instance_pool: Any | None = None,
    ) -> SessionContext:
        parsed_snapshot = parse_session_snapshot(snapshot, path="session_context.json")
        metadata_map = _normalize_session_metadata(parsed_snapshot["metadata"])
        return cls.build(
            storage=storage,
            num_workers=parsed_snapshot["num_workers"],
            instance_pool=instance_pool,
            run_id=parsed_snapshot["run_id"],
            metadata=metadata_map,
        )

    @classmethod
    def load_from_storage(
        cls,
        *,
        storage: RunStoragePort,
        instance_pool: Any | None = None,
        filename: str = "session_context.json",
    ) -> SessionContext | None:
        payload = storage.read_json(filename)
        if payload is None:
            return None
        return cls.from_snapshot(storage=storage, snapshot=payload, instance_pool=instance_pool)


__all__ = [
    "SessionContext",
]
