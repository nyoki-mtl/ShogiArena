"""Preflight helpers for tournament game execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from rshogi.core import normalize_usi_position

from shogiarena._core.contexts.game_session.application.progress.payload_parser import enqueue_progress_event


class TimeControlSpecPort(Protocol):
    """Minimal time-control contract used by progress payload serialization."""

    def to_spec_str(self) -> str: ...


def should_skip_cancelled_game(
    *,
    game_id: str,
    cancelled_provider: Callable[[], set[str]] | None,
    logger: logging.Logger,
) -> bool:
    """キャンセル済みゲームをスキップすべきか判定する。"""

    if cancelled_provider is None:
        return False
    try:
        cancelled_ids = cancelled_provider()
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning(
            "Failed to query cancelled games; proceeding with %s: %s",
            game_id,
            exc,
            exc_info=True,
        )
        return False
    return game_id in cancelled_ids


def emit_game_assigned_event(
    *,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
    game_id: str,
    initial_sfen: str | None,
    black_name: str,
    white_name: str,
    black_limits: TimeControlSpecPort | None,
    white_limits: TimeControlSpecPort | None,
) -> None:
    """ゲーム割り当てイベントをプログレスキューに送信する。"""

    if progress_queue is None:
        return
    initial_position = normalize_usi_position(initial_sfen or "startpos")
    black_tc_spec = black_limits.to_spec_str() if black_limits is not None else None
    white_tc_spec = white_limits.to_spec_str() if white_limits is not None else None
    enqueue_progress_event(
        progress_queue,
        game_id,
        {
            "type": "game_assigned",
            "game_id": game_id,
            "initial_sfen": initial_position,
            "black_name": black_name,
            "white_name": white_name,
            "time_control_black": black_tc_spec,
            "time_control_white": white_tc_spec,
        },
        fallback_move_count=0,
        should_allow_default_str=True,
    )


__all__ = ["emit_game_assigned_event", "should_skip_cancelled_game"]
