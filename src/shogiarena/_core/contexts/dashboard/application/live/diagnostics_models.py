"""Model/default definitions for live diagnostics guideline configs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_optional_text

_InputValue = JsonValue


class _ThresholdConfig(TypedDict):
    warning: float
    critical: float


class _HydratorConfig(TypedDict):
    trigger_per_hour: _ThresholdConfig
    failure_rate: _ThresholdConfig


class WatchlistExtra(TypedDict):
    key: str
    label: str
    unit: str
    warning: float
    critical: float
    should_notify: bool


class _WatchlistConfig(TypedDict):
    limit: int
    extras: list[WatchlistExtra]


class _AutoSnapshotConfig(TypedDict):
    interval_seconds: int
    mode: str
    destination: str
    retention_minutes: int


class _GuidelinesConfig(TypedDict):
    hydrator: _HydratorConfig
    watchlist: _WatchlistConfig
    auto_snapshot: _AutoSnapshotConfig


class _ThresholdModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    warning: float
    critical: float


class _HydratorModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trigger_per_hour: _ThresholdModel
    failure_rate: _ThresholdModel


class _WatchlistExtraModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    unit: str
    warning: float
    critical: float
    should_notify: bool = False

    @model_validator(mode="after")
    def _normalize_critical(self) -> _WatchlistExtraModel:
        if self.critical < self.warning:
            self.critical = self.warning
        return self


class _WatchlistModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = Field(ge=1)
    extras: list[_WatchlistExtraModel]


class _AutoSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_seconds: int = Field(ge=0)
    mode: str
    destination: str
    retention_minutes: int = Field(ge=0)

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: _InputValue) -> str:
        normalized = (coerce_optional_text(value) or "console").lower()
        return "clipboard" if normalized == "clipboard" else "console"

    @field_validator("destination", mode="before")
    @classmethod
    def _normalize_destination(cls, value: _InputValue) -> str:
        normalized = (coerce_optional_text(value) or "console").lower()
        if normalized in {"console", "clipboard", "api"}:
            return normalized
        return "console"


class _GuidelinesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hydrator: _HydratorModel
    watchlist: _WatchlistModel
    auto_snapshot: _AutoSnapshotModel


DEFAULT_GUIDELINES: _GuidelinesConfig = {
    "hydrator": {
        "trigger_per_hour": {"warning": 45, "critical": 60},
        "failure_rate": {"warning": 0.03, "critical": 0.05},
    },
    "watchlist": {
        "limit": 6,
        "extras": [
            {
                "key": "payload_kb",
                "label": "Payload (KB)",
                "unit": "KB",
                "warning": 200,
                "critical": 320,
                "should_notify": False,
            },
            {
                "key": "latency_ms",
                "label": "Latency (ms)",
                "unit": "ms",
                "warning": 500,
                "critical": 1500,
                "should_notify": False,
            },
            {
                "key": "retries",
                "label": "Retries",
                "unit": "",
                "warning": 1,
                "critical": 3,
                "should_notify": False,
            },
            {
                "key": "detail_payload_kb_slim",
                "label": "Detail slim payload (KB)",
                "unit": "KB",
                "warning": 180,
                "critical": 240,
                "should_notify": False,
            },
            {
                "key": "detail_payload_kb_full",
                "label": "Detail full payload (KB)",
                "unit": "KB",
                "warning": 220,
                "critical": 300,
                "should_notify": False,
            },
            {
                "key": "detail_include_count",
                "label": "Detail include count",
                "unit": "fields",
                "warning": 4,
                "critical": 6,
                "should_notify": False,
            },
        ],
    },
    "auto_snapshot": {
        "interval_seconds": 0,
        "mode": "console",
        "destination": "console",
        "retention_minutes": 0,
    },
}


def _guidelines_from_model(model: _GuidelinesModel) -> _GuidelinesConfig:
    extras: list[WatchlistExtra] = []
    for extra in model.watchlist.extras:
        extras.append(
            {
                "key": extra.key,
                "label": extra.label,
                "unit": extra.unit,
                "warning": extra.warning,
                "critical": extra.critical,
                "should_notify": extra.should_notify,
            }
        )

    hydrator: _HydratorConfig = {
        "trigger_per_hour": {
            "warning": model.hydrator.trigger_per_hour.warning,
            "critical": model.hydrator.trigger_per_hour.critical,
        },
        "failure_rate": {
            "warning": model.hydrator.failure_rate.warning,
            "critical": model.hydrator.failure_rate.critical,
        },
    }
    watchlist: _WatchlistConfig = {
        "limit": model.watchlist.limit,
        "extras": extras,
    }
    auto_snapshot: _AutoSnapshotConfig = {
        "interval_seconds": model.auto_snapshot.interval_seconds,
        "mode": model.auto_snapshot.mode,
        "destination": model.auto_snapshot.destination,
        "retention_minutes": model.auto_snapshot.retention_minutes,
    }
    return {
        "hydrator": hydrator,
        "watchlist": watchlist,
        "auto_snapshot": auto_snapshot,
    }


def deep_copy_guidelines(data: Mapping[str, JsonValue] | None = None) -> _GuidelinesConfig:
    base = DEFAULT_GUIDELINES if data is None else data
    try:
        parsed = _GuidelinesModel.model_validate(base)
    except ValidationError:
        parsed = _GuidelinesModel.model_validate(DEFAULT_GUIDELINES)
    return _guidelines_from_model(parsed)


__all__ = [
    "DEFAULT_GUIDELINES",
    "WatchlistExtra",
    "_GuidelinesConfig",
    "_GuidelinesModel",
    "deep_copy_guidelines",
    "_guidelines_from_model",
]
