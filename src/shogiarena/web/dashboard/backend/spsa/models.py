"""SPSA ダッシュボード用 Pydantic I/O 境界モデル。

``SpsaStore`` が読み込む JSON/JSONL ファイルのスキーマを定義する。
各モデルは ``ConfigDict(extra="ignore")`` を使用し、未知フィールドを許容する。
全フィールドに安全なデフォルトを設定し、古い形式のファイルでもパース可能。

TypedDict (``spsa/types.py``) は API レスポンス型 (出力側) として残す。
こちらは入力パース専用。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shogiarena.utils.types.coerce import coerce_float

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class EngineStatEntry(BaseModel):
    """エンジン統計エントリ（meta.json の engine_stats 値）。"""

    wins: int = 0
    losses: int = 0
    draws: int = 0
    games: int = 0

    model_config = ConfigDict(extra="ignore")


class LtcRegressionMetaConfig(BaseModel):
    """meta.json の ltc_regression セクション。"""

    enabled: bool = False
    pairs: int | None = None
    sprt_elo0: float | None = None
    sprt_elo1: float | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return bool(v)
        return False


class LtcRegressionIndexInfo(BaseModel):
    """index.json metadata 内の ltc_regression セクション。"""

    status: str | None = None
    last_update_idx: int | None = None
    winrate: float | None = None
    elo: float | None = None
    pairs_played: int | None = None
    tuned_wins: int | None = None
    baseline_wins: int | None = None
    draws: int | None = None
    total_games: int | None = None
    timestamp: int | None = None
    baseline_update_idx: int | None = None
    baseline_variant_token: str | None = None
    tuned_variant_token: str | None = None
    accepted: bool | None = None
    sprt: dict[str, Any] | None = None
    sprt_decision: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("sprt", mode="before")
    @classmethod
    def _coerce_sprt(cls, v: object) -> dict[str, Any] | None:
        return {str(k): val for k, val in v.items()} if isinstance(v, dict) else None


# ---------------------------------------------------------------------------
# meta.json
# ---------------------------------------------------------------------------


def _coerce_float_dict(v: object) -> dict[str, float]:
    """dict[str, Any] を dict[str, float] に強制変換する。"""
    if not isinstance(v, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in v.items():
        cf = coerce_float(value)
        if cf is not None:
            result[str(key)] = cf
    return result


class SpsaMetaData(BaseModel):
    """meta.json のトップレベル構造（I/O 境界パース用）。"""

    session_uuid: str | None = None
    num_updates: int | None = None
    total: int | None = None
    experiment_name: str | None = None
    initial_params: dict[str, float] = Field(default_factory=dict)
    parameters_path: str | None = None

    # Engine metadata
    engine_time_controls: dict[str, str] = Field(default_factory=dict)
    default_time_control: str | None = None
    engines: list[str] = Field(default_factory=list)
    engine_instances: dict[str, str | None] = Field(default_factory=dict)
    engine_stats: dict[str, EngineStatEntry] = Field(default_factory=dict)
    engines_meta: list[dict[str, Any]] = Field(default_factory=list)

    # SPSA config keys
    mobility: float | None = None
    scale: float | None = None
    a0: float | None = None
    spsa_A: float | None = Field(None, alias="A")
    alpha: float | None = None
    gamma: float | None = None
    crn_enabled: bool | None = None
    int_rounding: str | None = None
    int_ck_floor: float | None = None
    update_mode: str | None = None
    snap_float_to_step: bool | None = None
    early_stop: dict[str, Any] | None = None
    update_batch_size: int | None = None
    inflight_factor: float | None = None

    # LTC
    ltc_regression: LtcRegressionMetaConfig | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_camel_case_keys(cls, data: Any) -> Any:
        """camelCase キーを snake_case に正規化する。"""
        if not isinstance(data, dict):
            return data
        mapping = {
            "engineTimeControls": "engine_time_controls",
            "defaultTimeControl": "default_time_control",
            "engineInstances": "engine_instances",
            "engineStats": "engine_stats",
            "enginesMeta": "engines_meta",
        }
        for camel, snake in mapping.items():
            if camel in data and snake not in data:
                data[snake] = data[camel]
        return data

    @field_validator("session_uuid", mode="before")
    @classmethod
    def _strip_uuid(cls, v: object) -> str | None:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return None

    @field_validator("initial_params", mode="before")
    @classmethod
    def _coerce_initial_params(cls, v: object) -> dict[str, float]:
        return _coerce_float_dict(v)

    @field_validator("engine_time_controls", mode="before")
    @classmethod
    def _coerce_time_controls(cls, v: object) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, str] = {}
        for name, spec in v.items():
            key = str(name or "").strip()
            if not key:
                continue
            result[key] = "-" if spec is None else str(spec)
        return result

    @field_validator("engines", mode="before")
    @classmethod
    def _coerce_engines(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(name).strip() for name in v if str(name or "").strip()]

    @field_validator("engine_instances", mode="before")
    @classmethod
    def _coerce_instances(cls, v: object) -> dict[str, str | None]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, str | None] = {}
        for name, inst in v.items():
            key = str(name or "").strip()
            if not key:
                continue
            result[key] = None if inst is None else str(inst)
        return result

    @field_validator("engine_stats", mode="before")
    @classmethod
    def _coerce_stats(cls, v: object) -> dict[str, Any]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, Any] = {}
        for name, stat in v.items():
            key = str(name or "").strip()
            if not key or not isinstance(stat, dict):
                continue
            result[key] = stat
        return result

    @field_validator("engines_meta", mode="before")
    @classmethod
    def _coerce_engines_meta(cls, v: object) -> list[dict[str, Any]]:
        if not isinstance(v, list):
            return []
        return [dict(entry) for entry in v if isinstance(entry, dict)]

    @field_validator("ltc_regression", mode="before")
    @classmethod
    def _coerce_ltc(cls, v: object) -> dict[str, Any] | None:
        return {str(k): val for k, val in v.items()} if isinstance(v, dict) else None

    @property
    def effective_num_updates(self) -> int | None:
        """num_updates または total のうち利用可能な方を返す。"""
        return self.num_updates if self.num_updates is not None else self.total

    def resolve_spsa_config(self) -> dict[str, Any] | None:
        """Rules タブ表示用の SPSA 設定 dict を返す。"""
        _KEYS = (
            "num_updates",
            "mobility",
            "scale",
            "a0",
            "alpha",
            "gamma",
            "crn_enabled",
            "int_rounding",
            "int_ck_floor",
            "update_mode",
            "snap_float_to_step",
            "early_stop",
            "update_batch_size",
            "inflight_factor",
        )
        config: dict[str, Any] = {}
        data = self.model_dump(by_alias=False)
        for key in _KEYS:
            if key in data and data[key] is not None:
                config[key] = data[key]
        # "A" は by_alias=False だと "spsa_A" になるため個別対応
        if self.spsa_A is not None:
            config["A"] = self.spsa_A
        if self.ltc_regression is not None:
            config["ltc_regression"] = self.ltc_regression.model_dump(exclude_none=True)
        return config or None


# ---------------------------------------------------------------------------
# index.json
# ---------------------------------------------------------------------------


def _coerce_perturbations(v: object) -> dict[str, dict[str, float]]:
    """perturbations dict を正規化する。"""
    if not isinstance(v, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for key, inner in v.items():
        if isinstance(inner, dict):
            coerced: dict[str, float] = {}
            for k2, v2 in inner.items():
                cf = coerce_float(v2)
                if cf is not None:
                    coerced[str(k2)] = cf
            result[str(key)] = coerced
    return result


class RawIndexUpdateEntry(BaseModel):
    """index.json の updates[] 配列内の個別エントリ（I/O 境界パース用）。

    ``event_service`` が構築する enriched ``UpdateEntry`` TypedDict とは異なり、
    こちらは index.json から読み込んだ生データのみを表す。
    """

    update_idx: int
    timestamp: int | None = None
    params: dict[str, float] = Field(default_factory=dict)
    s_plus: float | None = None
    s_minus: float | None = None
    step: float | None = None
    gradients: dict[str, float] = Field(default_factory=dict)
    deltas: dict[str, float] = Field(default_factory=dict)
    delta_norm: float | None = None
    batch_size: int | None = None
    total_games: int | None = None
    a_k: float | None = None
    c_k: float | None = None
    iteration_k: int | None = None
    perturbations: dict[str, dict[str, float]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")

    @field_validator("params", "gradients", "deltas", mode="before")
    @classmethod
    def _coerce_float_dict(cls, v: object) -> dict[str, float]:
        return _coerce_float_dict(v)

    @field_validator("perturbations", mode="before")
    @classmethod
    def _coerce_perturbations(cls, v: object) -> dict[str, dict[str, float]]:
        return _coerce_perturbations(v)

    def to_update_entry_dict(self) -> dict[str, Any]:
        """既存 ``UpdateEntry`` TypedDict 互換の dict を返す。"""
        return self.model_dump()


class IndexMetadata(BaseModel):
    """index.json の metadata セクション（I/O 境界パース用）。"""

    last_update_idx: int | None = None
    total_updates: int | None = None
    last_updated: int | None = None
    int_rounding_policy: str | None = None
    crn_used: bool | None = None
    update_mode: str | None = None
    ltc_regression: LtcRegressionIndexInfo | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("ltc_regression", mode="before")
    @classmethod
    def _coerce_ltc(cls, v: object) -> dict[str, Any] | None:
        return {str(k): val for k, val in v.items()} if isinstance(v, dict) else None


class IndexData(BaseModel):
    """index.json のトップレベル構造。"""

    updates: list[RawIndexUpdateEntry] = Field(default_factory=list)
    metadata: IndexMetadata = Field(default_factory=IndexMetadata)

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Summary cache
# ---------------------------------------------------------------------------


class SummaryCachePayload(BaseModel):
    """summary_cache_v1.json のキャッシュペイロード。"""

    version: int = 1
    session_uuid: str | None = None
    events_offset: int = 0
    events_size: int = 0
    events_mtime_ns: int = 0
    aggregates: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")
