"""Shared engine I/O event models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

UsiIoDirection: TypeAlias = Literal["in", "out", "stderr"]


@dataclass(frozen=True, slots=True)
class UsiIoEvent:
    """Typed USI I/O event."""

    direction: UsiIoDirection
    line: str
    monotonic_ns: int | None = None
    phase: str | None = None
    timestamp_ms: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Canonical field names の dict を返す。"""

        return {
            "direction": self.direction,
            "line": self.line,
            "monotonic_ns": self.monotonic_ns,
            "phase": self.phase,
            "timestamp_ms": self.timestamp_ms,
        }


__all__ = ["UsiIoDirection", "UsiIoEvent"]
