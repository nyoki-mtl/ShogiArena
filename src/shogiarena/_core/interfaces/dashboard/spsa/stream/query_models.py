"""Query parsing models for SPSA stream endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.interfaces.dashboard.api_query_models import (
    QueryValue,
    SummaryStreamQuery,
    _parse_bool_query,
)
from shogiarena._core.interfaces.dashboard.ws_hub.hub_message_models import DashboardWsClientMessage
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int


class TargetsStreamQuery(SummaryStreamQuery):
    update_idx: str | None = None
    limit: int | None = None
    poll_interval: float = 2.0

    @field_validator("limit", mode="before")
    @classmethod
    def _coerce_limit(cls, value: QueryValue) -> int | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("limit must be an integer")
        return parsed


class UpdatesStreamQuery(SummaryStreamQuery):
    poll_interval: float = 2.0


class WebSocketQuery(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    last_update_idx: int = -1
    should_send_initial: bool = Field(default=True, alias="send_initial")

    @field_validator("last_update_idx", mode="before")
    @classmethod
    def _coerce_last_update_idx(cls, value: QueryValue) -> int:
        if value is None:
            return -1
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("last_update_idx must be an integer")
        return parsed

    @field_validator("should_send_initial", mode="before")
    @classmethod
    def _coerce_send_initial(cls, value: QueryValue) -> bool:
        return _parse_bool_query(value, is_default=True)


class SpsaWebSocketClientMessage(DashboardWsClientMessage):
    last_update_idx: int | None = None


class ConvergenceStreamQuery(SummaryStreamQuery):
    poll_interval: float = 2.0
    ltc_limit: int = 200

    @field_validator("ltc_limit", mode="before")
    @classmethod
    def _coerce_ltc_limit(cls, value: QueryValue) -> int:
        if value is None:
            return 200
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("ltc_limit must be an integer")
        return parsed


class SpsaSummaryStreamQuery(SummaryStreamQuery):
    poll_interval: float = 15.0


class LtcResultsStreamQuery(SummaryStreamQuery):
    limit: int = 100
    poll_interval: float = 5.0

    @field_validator("limit", mode="before")
    @classmethod
    def _coerce_limit(cls, value: QueryValue) -> int:
        if value is None:
            return 100
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("limit must be an integer")
        return parsed


@dataclass(slots=True)
class _ResolvedTargetsQuery:
    targets: list[int]
    has_dynamic_targets: bool
    target_limit: int


def resolve_targets_query(
    parsed_query: TargetsStreamQuery,
    *,
    default_limit: int,
    max_targets: int,
    resolve_dynamic_targets: Callable[[int], list[int]],
) -> _ResolvedTargetsQuery:
    idx_param = parsed_query.update_idx
    if idx_param:
        raw_tokens = [token.strip() for token in idx_param.split(",") if token.strip()]
        parsed: list[int] = []
        for token in raw_tokens:
            value = coerce_int(token)
            if value is None:
                raise ValueError("update_idx must be int")
            if value < 0:
                raise ValueError("update_idx must be non-negative")
            parsed.append(value)
        return _ResolvedTargetsQuery(
            targets=sorted(set(parsed))[:max_targets],
            has_dynamic_targets=False,
            target_limit=default_limit,
        )

    target_limit = parsed_query.limit if parsed_query.limit is not None else default_limit
    target_limit = max(1, min(target_limit, max_targets))
    return _ResolvedTargetsQuery(
        targets=resolve_dynamic_targets(target_limit),
        has_dynamic_targets=True,
        target_limit=target_limit,
    )


__all__ = [
    "ConvergenceStreamQuery",
    "LtcResultsStreamQuery",
    "SpsaSummaryStreamQuery",
    "SpsaWebSocketClientMessage",
    "TargetsStreamQuery",
    "UpdatesStreamQuery",
    "WebSocketQuery",
    "resolve_targets_query",
]
