"""Contract for pushing folded CSA state onto the dashboard live stream.

The port names exactly the methods the dashboard API server already exposes, so
the publisher reaches the existing broadcast path without a new event type and
without the context depending on ``interfaces``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonValue

CSA_SUMMARY_SOURCE = "csa"


class CsaLiveStreamPort(Protocol):
    """Broadcast surface used by the CSA publisher."""

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, JsonValue]) -> None: ...

    def set_worker_snapshot(
        self,
        worker_idx: int,
        snapshot: Mapping[str, JsonValue],
        *,
        should_broadcast: bool = True,
    ) -> None: ...

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None: ...

    def broadcast_games_snapshot(self, snapshot: Mapping[str, JsonValue], *, event_type: str = "bulk") -> None: ...

    def broadcast_summary_update(self, payload: Mapping[str, JsonValue], *, source: str = "tournament") -> None: ...


__all__ = ["CSA_SUMMARY_SOURCE", "CsaLiveStreamPort"]
