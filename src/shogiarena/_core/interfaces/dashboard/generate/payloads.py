"""生成モードダッシュボードバックエンドで使用される型定義。"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

_RuleScalar = str | int | float | bool | None
_RuleValue = _RuleScalar | list[_RuleScalar] | dict[str, _RuleScalar]

# ---------------------------------------------------------------------------
# API レスポンス (TypedDict)
# ---------------------------------------------------------------------------


class GenerateSummary(TypedDict, total=False):
    """``get_summary`` が返す生成モードサマリ。"""

    total_games: int
    total_positions: int
    total_bytes: int
    file_count: int
    run_dir: str
    tournament_type: str
    mode: str
    record_format: str | None
    output_dir: str | None
    file_prefix: str | None
    rules: JsonObject | None
    run_status: str | None
    is_resumable: bool


# ---------------------------------------------------------------------------
# I/O 境界モデル – state.json / records_manifest.json のパース用
# ---------------------------------------------------------------------------


class RecordsOutputState(BaseModel):
    """``state.json`` の ``config.records_output`` セクション。"""

    output_dir: str | None = None
    format: str | None = None
    file_prefix: str | None = None

    model_config = ConfigDict(extra="ignore")


class RunStateConfig(BaseModel):
    """``state.json`` の ``config`` セクション。"""

    records_output: RecordsOutputState = Field(default_factory=RecordsOutputState)
    rules: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("rules", mode="before")
    @classmethod
    def _coerce_rules(cls, v: _RuleValue | None) -> dict[str, Any] | None:
        if not isinstance(v, dict):
            return None
        return {str(key): json_serialize(value) for key, value in v.items()}


class RunState(BaseModel):
    """``state.json`` のトップレベル構造。"""

    config: RunStateConfig = Field(default_factory=RunStateConfig)

    model_config = ConfigDict(extra="ignore")


class RecordsManifest(BaseModel):
    """``records_manifest.json`` の構造。"""

    files: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @field_validator("files", mode="before")
    @classmethod
    def _coerce_files(cls, v: _RuleValue | list[object] | None) -> list[dict[str, Any]]:
        if not isinstance(v, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            normalized.append({str(key): json_serialize(value) for key, value in item.items()})
        return normalized
