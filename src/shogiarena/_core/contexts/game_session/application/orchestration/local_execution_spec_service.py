"""Local execution spec assembly helpers shared across orchestrators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.ports.session_runner_ports import BeforeGameHookPort

TEngineItem = TypeVar("TEngineItem")
TGameExecutionSpec = TypeVar("TGameExecutionSpec")

OnGameStartHook = Callable[[], Awaitable[None]]
GameExecutionSpecFactory = Callable[..., TGameExecutionSpec]


@dataclass(frozen=True)
class LocalExecutionSpecRequest(Generic[TEngineItem]):
    """Normalized local execution-spec input."""

    black_item: TEngineItem
    white_item: TEngineItem
    initial_sfen: str | None
    game_id: str
    black_limits: object
    white_limits: object
    game_round: int | None = None
    before_game_hook: BeforeGameHookPort | None = None
    on_game_start: OnGameStartHook | None = None


def build_local_execution_spec(
    *,
    request: LocalExecutionSpecRequest[TEngineItem],
    game_execution_spec_factory: GameExecutionSpecFactory[TGameExecutionSpec],
) -> TGameExecutionSpec:
    """ローカル実行スペックを組み立てる。"""

    payload: dict[str, object] = {
        "black_item": request.black_item,
        "white_item": request.white_item,
        "initial_sfen": request.initial_sfen,
        "game_id": request.game_id,
        "black_limits": request.black_limits,
        "white_limits": request.white_limits,
    }
    if request.game_round is not None:
        payload["game_round"] = request.game_round
    if request.before_game_hook is not None:
        payload["before_game_hook"] = request.before_game_hook
    if request.on_game_start is not None:
        payload["on_game_start"] = request.on_game_start
    return game_execution_spec_factory(**payload)


__all__ = ["LocalExecutionSpecRequest", "build_local_execution_spec"]
