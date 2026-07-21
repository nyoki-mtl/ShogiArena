"""OpenBench completion-context assembly service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from shogiarena._core.contexts.game_session.ports.completion_runtime import CompletionOpenBenchContext


class CompletionOpenBenchClientPort(Protocol):
    """Minimal OpenBench client contract required by completion wiring."""

    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def is_strict(self) -> bool: ...


class CompletionOpenBenchContextService:
    """Build CompletionOpenBenchContext from runner-provided wiring."""

    @staticmethod
    def is_strict_mode(client: CompletionOpenBenchClientPort | None) -> bool:
        return client is not None and client.is_strict

    def build_context(
        self,
        *,
        client: CompletionOpenBenchClientPort | None,
        sync_after_game: Callable[[], Awaitable[None]],
    ) -> CompletionOpenBenchContext:
        return CompletionOpenBenchContext(
            is_strict_mode=self.is_strict_mode(client),
            sync_after_game=sync_after_game,
        )


__all__ = ["CompletionOpenBenchClientPort", "CompletionOpenBenchContextService"]
