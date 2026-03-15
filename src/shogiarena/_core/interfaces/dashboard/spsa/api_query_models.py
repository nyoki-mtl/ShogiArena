"""Query/path models used by SPSA HTTP API handlers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SpsaEventsQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    limit: int = Field(default=200, ge=1, le=5000)


class SpsaParamsQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    variant_id: str | None = None


class SpsaUpdatePath(BaseModel):
    model_config = ConfigDict(extra="ignore")
    idx: int = Field(ge=0)


class LtcResultsQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    limit: int = Field(default=50, ge=1, le=500)


class SpsaUpdatesQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class LtcLimitQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ltc_limit: int = Field(default=200, ge=1, le=500)


__all__ = [
    "LtcLimitQuery",
    "LtcResultsQuery",
    "SpsaEventsQuery",
    "SpsaParamsQuery",
    "SpsaUpdatePath",
    "SpsaUpdatesQuery",
]
