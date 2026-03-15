"""Shared query models for dashboard HTTP APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

QueryValue = str | int | float | bool | None


def _parse_float_query(value: QueryValue, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return float(stripped)
        except ValueError as exc:
            raise ValueError("poll_interval must be a number") from exc
    if isinstance(value, int | float):
        return float(value)
    raise TypeError("poll_interval must be a number")


def _parse_bool_query(value: QueryValue, *, is_default: bool) -> bool:
    if value is None:
        return is_default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() != "false"
    raise TypeError("send_initial must be a boolean")


class PaginatedSearchQuery(BaseModel):
    """Shared pagination + free-text search query for games endpoints."""

    model_config = ConfigDict(extra="ignore")

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)
    q: str = ""

    @field_validator("q", mode="before")
    @classmethod
    def _coerce_q(cls, value: QueryValue) -> str:
        if value is None:
            return ""
        return str(value)


class SummaryStreamQuery(BaseModel):
    """Shared query for summary stream endpoints."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    poll_interval: float = 5.0
    should_send_initial: bool = Field(default=True, alias="send_initial")

    @classmethod
    def _poll_interval_default(cls) -> float:
        field = cls.model_fields.get("poll_interval")
        default = field.default if field is not None else 5.0
        return float(default)

    @classmethod
    def _send_initial_default(cls) -> bool:
        field = cls.model_fields.get("should_send_initial")
        default = field.default if field is not None else True
        return bool(default)

    @field_validator("poll_interval", mode="before")
    @classmethod
    def _coerce_poll_interval(cls, value: QueryValue) -> float:
        return _parse_float_query(value, default=cls._poll_interval_default())

    @field_validator("should_send_initial", mode="before")
    @classmethod
    def _coerce_send_initial(cls, value: QueryValue) -> bool:
        return _parse_bool_query(value, is_default=cls._send_initial_default())


__all__ = [
    "PaginatedSearchQuery",
    "QueryValue",
    "SummaryStreamQuery",
]
