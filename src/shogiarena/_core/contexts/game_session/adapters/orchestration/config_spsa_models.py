"""SPSA run configuration models."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shogiarena._core.contexts.game_session.application.engine.config_normalizer import EngineSyncStrategy
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .config_core import RulesConfig, SprtConfig
from .config_engine import DashboardConfig, EngineConfig, SystemConfig


class _DashboardPayload(TypedDict, total=False):
    is_enabled: bool
    api_port: int
    api_host: str


class _EngineCommonKwargs(TypedDict):
    mate_default_ply_limit: int | None
    mate_default_node_limit: int | None
    is_mate_default_infinite: bool
    should_mate_wait_for_bestmove: bool
    isready_sync_strategy: EngineSyncStrategy
    isready_lock_key: str | None
    isready_lock_template: str | None
    isready_lock_check_key: str | None
    isready_lock_check_template: str | None
    isready_lock_check_templates: tuple[str, ...]
    should_skip_isready_lock_if_exists: bool
    time_control: TimeControlLimits | None
    options_overlays: list[Path]
    instance_id: str | None


class LtcPassCriteria(BaseModel):
    """LTC test pass criteria."""

    min_winrate: float | None = None
    max_elo_drop: float | None = None
    sprt: SprtConfig | None = None

    @model_validator(mode="after")
    def _validate_pass_criteria(self) -> Self:
        if self.min_winrate is not None and not (0.0 <= self.min_winrate <= 1.0):
            raise ValueError("ltc_pass_criteria.min_winrate must be between 0.0 and 1.0")
        return self


class LtcRegressionConfig(BaseModel):
    """LTC regression test configuration."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="forbid")

    is_enabled: bool = Field(default=False, alias="enabled")
    every_n_updates: int = Field(default=0, ge=0)
    total_pairs: int = Field(default=0, ge=0)
    time_control: TimeControlLimits | None = None
    pass_criteria: LtcPassCriteria | None = None

    @model_validator(mode="after")
    def _validate_ltc_regression(self) -> Self:
        if self.is_enabled:
            if self.every_n_updates <= 0:
                raise ValueError("ltc_regression.every_n_updates must be positive when enabled")
            if self.total_pairs <= 0:
                raise ValueError("ltc_regression.total_pairs must be positive when enabled")
        return self


class EarlyStopConfig(BaseModel):
    """SPSA early-stop configuration."""

    type: Literal["delta_norm"] = "delta_norm"
    threshold: float = Field(default=1e-3, gt=0)


class SpsaAlgorithmAConfig(BaseModel):
    """Classic SPSA A schedule configuration."""

    mode: Literal["absolute", "ratio"] = "absolute"
    value: float = Field(default=0.0, ge=0.0)


class SpsaAlgorithmBlock(BaseModel):
    """SPSA algorithm configuration block."""

    name: Literal["classic"] = "classic"
    alpha: float = 0.602
    gamma: float = 0.101
    A: SpsaAlgorithmAConfig = Field(default_factory=SpsaAlgorithmAConfig)


class SpsaVariantApplyConfig(BaseModel):
    """Variant option application behavior."""

    # alias 付きフィールドは populate_by_name がないとフィールド名指定が黙って捨てられる。
    model_config = ConfigDict(populate_by_name=True)

    is_clear_hash_enabled: bool = Field(default=True, alias="clear_hash")
    after_setoption: Literal["isready", "none"] = "isready"


class SpsaVariantsConfig(BaseModel):
    """SPSA variant generation and pairing configuration."""

    # alias 付きフィールドは populate_by_name がないとフィールド名指定が黙って捨てられる。
    model_config = ConfigDict(populate_by_name=True)

    pairing: Literal["plus_minus"] = "plus_minus"
    is_crn_enabled: bool = Field(default=True, alias="crn")
    integer_rounding: Literal["none", "stochastic"] = "stochastic"
    instance_affinity: Literal["update", "none"] = "update"
    apply: SpsaVariantApplyConfig = Field(default_factory=SpsaVariantApplyConfig)


class SpsaRunConfig(BaseModel):
    """SPSA run configuration."""

    # alias 付きフィールドは populate_by_name がないとフィールド名指定が黙って捨てられる。
    model_config = ConfigDict(populate_by_name=True)

    # Required inputs
    start_sfens_path: str
    space_path: str
    # Engines: exactly one entry required; baseline=tunedに同一を使用
    baseline: list[EngineConfig]
    tuned: list[EngineConfig]
    # Tournament-like rules (time_control, adjudication, etc.)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    # SPSA algorithm parameters
    num_updates: int = Field(gt=0)
    pairs_per_update: int = Field(default=1, gt=0)
    algorithm: SpsaAlgorithmBlock = Field(default_factory=SpsaAlgorithmBlock)
    variants: SpsaVariantsConfig = Field(default_factory=SpsaVariantsConfig)
    scale: float = 1.0
    # Paths and runtime
    experiment_name: str | None = None
    instances: tuple[Path, ...] | None = None
    # Async orchestration
    inflight_factor: int = 4
    update_batch_size: int | None = None
    is_snap_float_to_step: bool = Field(default=False, alias="snap_float_to_step")
    # OpenBench alignment options
    int_ck_floor: float = 0.5
    update_mode: Literal["immediate", "barrier"] = "immediate"
    early_stop: EarlyStopConfig | None = None
    # Dashboard / workers
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    num_workers: int = 1
    ltc_regression: LtcRegressionConfig | None = None

    @property
    def is_crn_enabled(self) -> bool:
        """Return whether CRN opening reuse is enabled."""
        return bool(self.variants.is_crn_enabled)

    @property
    def int_rounding(self) -> Literal["none", "stochastic"]:
        """Return integer rounding mode."""
        return self.variants.integer_rounding

    @property
    def alpha(self) -> float:
        """Return classic SPSA alpha."""
        return float(self.algorithm.alpha)

    @property
    def gamma(self) -> float:
        """Return classic SPSA gamma."""
        return float(self.algorithm.gamma)

    @property
    def algorithm_a(self) -> float | None:
        """Return absolute A for legacy callers when available."""
        if self.algorithm.A.mode == "absolute":
            return float(self.algorithm.A.value)
        return None
