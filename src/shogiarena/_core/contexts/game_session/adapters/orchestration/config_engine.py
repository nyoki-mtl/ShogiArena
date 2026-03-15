"""Engine and shared runtime model definitions for orchestration config."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shogiarena._core.contexts.game_session.application.engine.config_hashing import (
    CpuAffinityToken,
    hash_engine_config,
    parse_cpu_affinity_spec,
)
from shogiarena._core.contexts.game_session.application.engine.option_coercion import (
    EngineOptionMap,
)
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class EngineConfig(BaseModel):
    """Configuration for a single engine in the tournament."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    engine_path: Path | None = None
    artifact: str | None = None
    build_options: EngineOptionMap = Field(default_factory=dict)
    name: str | None = None
    name_style: Literal["hash", "short"] = "hash"
    options: EngineOptionMap = Field(default_factory=dict)
    options_overlays: list[Path] = Field(default_factory=list)
    mate_default_ply_limit: int | None = Field(default=None, gt=0)
    mate_default_node_limit: int | None = Field(default=None, gt=0)
    is_mate_default_infinite: bool = Field(default=False, alias="mate_default_infinite")
    should_mate_wait_for_bestmove: bool = Field(default=False, alias="mate_wait_for_bestmove")
    isready_sync_strategy: Literal["direct", "wait", "stop"] = "direct"
    isready_lock_key: str | None = None
    isready_lock_template: str | None = None
    isready_lock_check_key: str | None = None
    isready_lock_check_template: str | None = None
    isready_lock_check_templates: tuple[str, ...] = ()
    should_skip_isready_lock_if_exists: bool = Field(default=False, alias="isready_lock_skip_if_exists")
    handshake_timeout: float | None = Field(default=None, gt=0)
    time_control: TimeControlLimits | None = None
    instance_id: str | None = None
    cpu_affinity: tuple[int, ...] | None = None

    @field_validator("engine_path", mode="before")
    @classmethod
    def _resolve_engine_path(cls, v: JsonValue | Path | None) -> Path | None:
        if v is None:
            return None
        return Path(resolve_path_like(str(v)))

    @field_validator("options_overlays", mode="before")
    @classmethod
    def _normalize_options_overlays(
        cls,
        v: JsonValue | Path | list[JsonValue | Path] | tuple[JsonValue | Path, ...] | None,
    ) -> list[Path]:
        if v is None:
            return []
        normalized: list[Path] = []
        if isinstance(v, list | tuple):
            if not v:
                return []
            for raw in v:
                candidate = Path(resolve_path_like(str(raw)))
                if not candidate.is_absolute():
                    candidate = candidate.resolve()
                if not candidate.exists():
                    raise FileNotFoundError(f"Options overlay file not found: {candidate}")
                normalized.append(candidate)
            return normalized

        candidate = Path(resolve_path_like(str(v)))
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Options overlay file not found: {candidate}")
        normalized.append(candidate)
        return normalized

    @field_validator(
        "isready_lock_key",
        "isready_lock_template",
        "isready_lock_check_key",
        "isready_lock_check_template",
        mode="after",
    )
    @classmethod
    def _strip_optional_lock_str(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("isready_lock_check_templates", mode="before")
    @classmethod
    def _normalize_check_templates(cls, v: JsonValue | tuple[JsonValue, ...] | None) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, str):
            stripped = v.strip()
            return (stripped,) if stripped else ()
        if not isinstance(v, list | tuple):
            raise TypeError("isready_lock_check_templates must be a string or list of strings")
        normalized = [str(item).strip() for item in v]
        return tuple(item for item in normalized if item)

    @field_validator("cpu_affinity", mode="before")
    @classmethod
    def _normalize_cpu_affinity(cls, v: JsonValue | tuple[JsonValue, ...] | None) -> tuple[int, ...] | None:
        if v is None:
            return None
        if isinstance(v, str | int):
            parsed = parse_cpu_affinity_spec(v)
        elif isinstance(v, list | tuple):
            tokens: list[CpuAffinityToken] = []
            for token in v:
                if isinstance(token, str | int | float | bool):
                    tokens.append(token)
                    continue
                raise TypeError("cpu_affinity entries must be strings or numbers")
            parsed = parse_cpu_affinity_spec(tokens)
        else:
            raise TypeError("cpu_affinity must be a string, integer, or iterable")
        if not parsed:
            return None
        return parsed

    @model_validator(mode="after")
    def _validate_engine_config(self: EngineConfig) -> EngineConfig:
        # Mate defaults: at most one
        if self.mate_default_ply_limit is not None and self.mate_default_node_limit is not None:
            raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
        if self.is_mate_default_infinite and (
            self.mate_default_ply_limit is not None or self.mate_default_node_limit is not None
        ):
            raise ValueError("mate_default_infinite must not be combined with mate_default_ply_limit/node_limit")

        # isready_lock exclusivity
        if self.isready_lock_key and self.isready_lock_template:
            raise ValueError("Specify only one of isready_lock_key or isready_lock_template")
        if self.isready_lock_check_key and self.isready_lock_check_template:
            raise ValueError("Specify only one of isready_lock_check_key or isready_lock_check_template")
        if self.isready_lock_check_templates and (self.isready_lock_check_key or self.isready_lock_check_template):
            raise ValueError("isready_lock_check_templates must not be combined with isready_lock_check_key/template")

        # Auto-generate name if not provided
        if not self.name:
            if self.engine_path is not None:
                base_name = self.engine_path.name
            elif (art_name := coerce_str(self.artifact)) is not None:
                base_name = art_name
            else:
                base_name = "engine"
            hash_len = 6 if self.name_style == "short" else 8
            self.name = f"{base_name}@{hash_engine_config(self, length=hash_len)}"

        return self

    def load_overlay_options(self) -> JsonObject:
        merged: JsonObject = {}
        for overlay in self.options_overlays:
            if not overlay.exists():
                raise FileNotFoundError(f"Options overlay file not found: {overlay}")
            with open(overlay, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, Mapping):
                raise TypeError("options_overlays YAML must be a mapping")
            opts = raw.get("options") if "options" in raw else raw
            if isinstance(opts, Mapping):
                merged.update(coerce_json_object_serialized(opts, field_name="options"))
        return merged


class TournamentConfig(BaseModel):
    scheduler: str = "round_robin"
    games_per_pair: int = 4
    seed: int = 42
    num_parallel: int = 4
    game_order: Literal["auto", "pairwise", "interleave", "shuffle"] = "auto"
    baseline_count: int = 1


class GenerateConfig(BaseModel):
    """Configuration for generate (selfplay) runs."""

    games: int = Field(default=100, gt=0)
    seed: int = 42
    num_parallel: int = Field(default=4, gt=0)


class RatingConfig(BaseModel):
    initial: float = 1500.0
    k_factor: float = 16.0


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    is_enabled: bool = Field(default=True, alias="enabled")
    api_port: int = 8080


class SystemConfig(BaseModel):
    """System-level configuration placeholder."""

    resource_poll_interval: float = Field(default=0.1, gt=0)
    resource_poll_max_interval: float = Field(default=1.0, gt=0)
    engine_handshake_timeout: float | None = Field(default=None, gt=0)
    extras: EngineOptionMap = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_poll_intervals(self) -> Self:
        if self.resource_poll_interval > self.resource_poll_max_interval:
            raise ValueError("system.resource_poll_interval must be <= resource_poll_max_interval")
        return self


class RecordOutputConfig(BaseModel):
    """バイナリ棋譜の出力設定。"""

    format: Literal["psv", "sbinpack"]
    max_positions_per_file: int = Field(default=1_000_000, gt=0)
    max_games_per_file: int | None = Field(default=None, gt=0)
    output_dir: Path | None = None
    file_prefix: str | None = None

    @model_validator(mode="after")
    def _validate_record_output(self) -> Self:
        if self.file_prefix is not None and not str(self.file_prefix).strip():
            raise ValueError("records_output.file_prefix must be a non-empty string when provided")
        if self.output_dir is not None:
            self.output_dir = Path(self.output_dir)
        if self.format == "psv" and self.max_games_per_file is not None:
            raise ValueError("records_output.max_games_per_file is not supported for psv")
        return self
