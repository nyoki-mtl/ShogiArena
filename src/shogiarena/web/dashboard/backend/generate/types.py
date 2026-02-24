"""生成モードダッシュボードバックエンドで使用される型定義。"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# API レスポンス (TypedDict)
# ---------------------------------------------------------------------------


class GenerateSummary(TypedDict, total=False):
    """``get_summary`` が返す生成モードサマリ。"""

    totalGames: int
    totalPositions: int
    totalBytes: int
    fileCount: int
    runDir: str
    tournamentType: str
    mode: str
    recordFormat: str | None
    outputDir: str | None
    filePrefix: str | None
    rules: dict[str, object] | None


# ---------------------------------------------------------------------------
# I/O 境界モデル – run_state.json / records_manifest.json のパース用
# ---------------------------------------------------------------------------


class RecordsOutputState(BaseModel):
    """``run_state.json`` の ``config.records_output`` セクション。"""

    output_dir: str | None = None
    format: str | None = None
    file_prefix: str | None = None

    model_config = ConfigDict(extra="ignore")


class RunStateConfig(BaseModel):
    """``run_state.json`` の ``config`` セクション。"""

    records_output: RecordsOutputState = Field(default_factory=RecordsOutputState)
    rules: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("rules", mode="before")
    @classmethod
    def _coerce_rules(cls, v: object) -> dict[str, Any] | None:
        return {str(k): val for k, val in v.items()} if isinstance(v, dict) else None


class RunState(BaseModel):
    """``run_state.json`` のトップレベル構造。"""

    config: RunStateConfig = Field(default_factory=RunStateConfig)

    model_config = ConfigDict(extra="ignore")


class ManifestEntry(BaseModel):
    """``records_manifest.json`` の個別ファイルエントリ。"""

    games: int = 0
    positions: int = 0
    byte_count: int = Field(0, alias="bytes")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class RecordsManifest(BaseModel):
    """``records_manifest.json`` の構造。"""

    files: list[ManifestEntry] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
