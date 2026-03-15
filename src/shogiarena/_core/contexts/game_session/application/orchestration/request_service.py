"""Dispatch request assembly helpers for orchestrators."""

from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    RemoteDispatchRequest,
)


def build_dispatch_request(
    *,
    black_engine_name: str,
    white_engine_name: str,
    black_item_instance_override: str | None = None,
    white_item_instance_override: str | None = None,
    should_raise_on_missing_instance: bool = False,
) -> RemoteDispatchRequest:
    """Dispatch request DTO を組み立てる。"""

    return RemoteDispatchRequest(
        black_engine_name=black_engine_name,
        white_engine_name=white_engine_name,
        black_item_instance_override=black_item_instance_override,
        white_item_instance_override=white_item_instance_override,
        should_raise_on_missing_instance=should_raise_on_missing_instance,
    )


__all__ = ["build_dispatch_request"]
