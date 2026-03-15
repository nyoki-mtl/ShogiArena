"""Core boundary contract primitives."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shogiarena._core.shared.kernel.exceptions import ContractParseError

WireModelT = TypeVar("WireModelT", bound=BaseModel)


def parse_wire(
    *,
    boundary_id: str,
    payload: Any,
    model: type[WireModelT],
    path: str = "root",
) -> WireModelT:
    """Validate wire payload with a strict Pydantic boundary."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ContractParseError(
            boundary_id=boundary_id,
            path=path,
            field=None,
            raw_type=type(payload).__name__,
            message="Failed to parse wire payload",
            raw_value=payload,
        ) from exc


__all__ = [
    "parse_wire",
]
