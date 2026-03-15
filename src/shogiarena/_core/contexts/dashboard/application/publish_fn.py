"""Shared callable protocol for dashboard publish callbacks."""

from __future__ import annotations

from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject


class PublishFn(Protocol):
    """Protocol for websocket publish callback."""

    def __call__(
        self,
        topic: str,
        payload: JsonObject,
        *,
        worker_idx: int | None = None,
    ) -> None: ...


__all__ = ["PublishFn"]
