"""Run result payload codec helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shogiarena._core.contexts.game_session.ports.result_store import PersistedRunResultPort
from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object_or_none as _coerce_json_object_or_none,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(slots=True)
class _ResultStorePayload:
    """`save_result` が永続化するペイロード。"""

    run_id: str
    type: str
    run_dir: str
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    summary: JsonObject | None = None
    config_snapshot: JsonObject | None = None
    result: JsonObject = field(default_factory=dict)

    def to_json_payload(self) -> JsonObject:
        payload: JsonObject = {
            "run_id": self.run_id,
            "type": self.type,
            "run_dir": self.run_dir,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "result": self.result,
        }
        payload["summary"] = self.summary
        payload["config_snapshot"] = self.config_snapshot
        return payload


def _build_store_payload(result: PersistedRunResultPort) -> _ResultStorePayload:
    return _ResultStorePayload(
        run_id=result.run_id,
        type=type(result).__name__,
        run_dir=result.run_dir.as_posix(),
        started_at=_format_datetime(result.started_at),
        completed_at=_format_datetime(result.completed_at),
        duration_ms=result.duration_ms(),
        summary=_coerce_json_object_or_none(result.summary),
        config_snapshot=_coerce_json_object_or_none(result.config_snapshot),
        result=_serialize_result_extras(result),
    )


def serialize_run_result_payload(result: PersistedRunResultPort) -> JsonObject:
    return _build_store_payload(result).to_json_payload()


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _serialize_result_extras(result: PersistedRunResultPort) -> JsonObject:
    return {str(key): value for key, value in result.serialize_extras().items()}


__all__ = ["serialize_run_result_payload"]
