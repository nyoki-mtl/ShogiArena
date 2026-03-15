from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int


class _SpsaRunStatePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    completed_updates: int = 0
    total_updates: int = 0
    is_finished: bool = False
    updated_at: str | None = None

    @field_validator("completed_updates", "total_updates", mode="before")
    @classmethod
    def _coerce_non_negative_int(cls, value: Any | None) -> int:
        """None/変換失敗/負値はすべて 0 にフォールバック。"""
        parsed = coerce_int(value)
        if parsed is None:
            return 0
        return max(0, parsed)


__all__ = [
    "_SpsaRunStatePayload",
]
