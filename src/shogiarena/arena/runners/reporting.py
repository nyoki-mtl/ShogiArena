from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

ProgressPayload: TypeAlias = Mapping[str, object]


class ProgressReporter(Protocol):
    def on_game_start(self, payload: ProgressPayload) -> None: ...
    def on_game_complete(self, payload: ProgressPayload) -> None: ...
    def on_update(self, payload: ProgressPayload) -> None: ...
    def finalize(self, payload: ProgressPayload) -> None: ...


@dataclass(slots=True)
class NullProgressReporter:
    def on_game_start(self, payload: ProgressPayload) -> None:
        return

    def on_game_complete(self, payload: ProgressPayload) -> None:
        return

    def on_update(self, payload: ProgressPayload) -> None:
        return

    def finalize(self, payload: ProgressPayload) -> None:
        return


__all__ = ["ProgressReporter", "NullProgressReporter"]
