"""Shared boundary parse/serialize exceptions used across layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(eq=True)
class _TypedValidationError(ValueError):
    """Base exception for typed boundary violations."""

    boundary_id: str
    path: str | None
    field: str | None
    raw_type: str
    message: str

    def __post_init__(self) -> None:
        context = self._format_context()
        super().__init__(f"{self.message}{context}")

    def _format_context(self) -> str:
        details: list[str] = [f"[boundary={self.boundary_id}"]
        if self.path is not None:
            details.append(f"path={self.path}")
        if self.field is not None:
            details.append(f"field={self.field}")
        details.append(f"raw_type={self.raw_type}]")
        return " " + ", ".join(details)


@dataclass(eq=True)
class ContractParseError(_TypedValidationError):
    """Raised when Wire -> Domain conversion fails."""

    raw_value: Any | None = None


@dataclass(eq=True)
class ContractSerializeError(_TypedValidationError):
    """Raised when Domain -> Wire conversion fails."""

    raw_value: Any | None = None


__all__ = ["ContractParseError", "ContractSerializeError"]
