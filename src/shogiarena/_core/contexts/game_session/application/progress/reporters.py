"""Concrete progress reporter implementations."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import ProgressPayload


@dataclass(slots=True)
class NullProgressReporter:
    """No-op ProgressReporterPort implementation."""

    def on_game_start(self, payload: ProgressPayload) -> None:
        del payload

    def on_game_complete(self, payload: ProgressPayload) -> None:
        del payload

    def finalize(self, payload: ProgressPayload) -> None:
        del payload


__all__ = ["NullProgressReporter"]
