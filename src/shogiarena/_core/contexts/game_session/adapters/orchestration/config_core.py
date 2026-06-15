"""Configuration models and parser helpers for tournament/SPSA orchestrator flows."""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rshogi.core import parse_usi_position

from shogiarena._core.shared.kernel.game_results import STARTING_SFEN
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

logger = logging.getLogger(__name__)


def _strip_optional_string_value(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def parse_time_control_raw(raw: JsonValue | Mapping[str, JsonValue] | DictConfig | None) -> TimeControlLimits | None:
    if raw is None or not isinstance(raw, Mapping):
        return None
    container = raw if not isinstance(raw, DictConfig) else OmegaConf.to_container(raw, resolve=True)
    if not isinstance(container, dict):
        return None
    return TimeControlLimits.model_validate(container)


class InitialPositionConfig(BaseModel):
    """Configuration for initial position generation."""

    type: Literal["startpos", "file"] = "startpos"
    flip_policy: Literal["alternate", "random", "none", "pair_both"] = "pair_both"
    source: str | None = None

    def generate(self, num_positions: int, seed: str) -> list[str]:
        if self.type == "startpos":
            return self.generate_startpos(num_positions)
        if self.type == "file" and self.source:
            with open(self.source, encoding="utf-8") as f:
                raw_lines = [line.strip() for line in f if line.strip()]

            positions: list[str] = []
            for line in raw_lines:
                if line.startswith("sfen "):
                    positions.append(line[5:].strip())
                    continue
                if line.startswith("position sfen "):
                    positions.append(line[14:].strip())
                    continue
                if line == "startpos":
                    positions.append(STARTING_SFEN)
                    continue
                if line.startswith("position startpos") or line.startswith("startpos "):
                    b = parse_usi_position(line)
                    positions.append(b.to_sfen())
                    continue
                parts = line.split()
                if len(parts) >= 3 and "/" in parts[0]:
                    positions.append(line)
                    continue
                b = parse_usi_position(line)
                positions.append(b.to_sfen())

            rng = random.Random(seed)
            return [rng.choice(positions) for _ in range(num_positions)]

        logger.warning(
            "Unknown initial position type '%s' - falling back to startpos. Use 'startpos' or 'file'.",
            self.type,
        )
        return self.generate_startpos(num_positions)

    def generate_startpos(self, num_positions: int) -> list[str]:
        return [STARTING_SFEN] * num_positions


class AdjudicationSettings(BaseModel):
    """Adjudication knobs controlling resign and max-move policies."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    resign_threshold_cp: int | None = Field(default=None, gt=0)
    resign_move_count: int = 8
    is_resign_two_sided: bool = Field(default=True, alias="resign_two_sided")
    is_max_plies_enabled: bool = Field(default=True, alias="enable_max_plies")
    max_plies: int | None = Field(default=320, gt=0)
    should_sync_max_plies_with_engine: bool = Field(default=True, alias="sync_max_plies_with_engine")
    engine_max_ply_option_names: str | list[str] = "auto"

    @field_validator("engine_max_ply_option_names", mode="before")
    @classmethod
    def _normalize_option_names(cls, v: JsonValue | None) -> str | list[str]:
        if isinstance(v, str):
            trimmed = v.strip()
            return trimmed or "auto"
        if not isinstance(v, list | tuple):
            raise TypeError("engine_max_ply_option_names must be a string or list of strings")
        deduped: list[str] = []
        seen: set[str] = set()
        for n in v:
            val = str(n).strip()
            if val and val not in seen:
                seen.add(val)
                deduped.append(val)
        return deduped

    @model_validator(mode="after")
    def _validate_max_plies_consistency(self) -> Self:
        if self.is_max_plies_enabled:
            if self.max_plies is None:
                raise ValueError("adjudication.max_plies must be set when enable_max_plies is true")
        else:
            self.max_plies = None
        return self


class RulesConfig(BaseModel):
    """Game rules configuration."""

    time_control: TimeControlLimits | None = None
    initial_positions: InitialPositionConfig = Field(default_factory=InitialPositionConfig)
    adjudication: AdjudicationSettings = Field(default_factory=AdjudicationSettings)
    repetition_occurrences_to_draw: int = 2

    @field_validator("repetition_occurrences_to_draw")
    @classmethod
    def _validate_repetition(cls, v: int) -> int:
        if v not in (2, 3, 4):
            raise ValueError("repetition_occurrences_to_draw must be 2, 3, or 4")
        return v


class SprtConfig(BaseModel):
    """SPRT early stopping configuration."""

    model: Literal["gsprt-trinomial-v1", "gsprt-pentanomial-v1"] = "gsprt-trinomial-v1"
    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    min_games: int = Field(default=0, ge=0)
    max_games: int | None = Field(default=None, gt=0)
    num_parallel: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_sprt(self) -> Self:
        if self.max_games is not None and self.max_games < self.min_games:
            raise ValueError("sprt.max_games must be >= min_games")
        if self.elo1 <= self.elo0:
            raise ValueError("sprt.elo1 must be greater than elo0")
        return self


class OpenBenchCreatePayload(BaseModel):
    """Payload template for OpenBench/ShogiBench CREATE_TEST action."""

    dev_engine: str | None = None
    base_engine: str | None = None
    dev_repo: str | None = None
    base_repo: str | None = None
    dev_branch: str | None = None
    base_branch: str | None = None
    dev_bench: str = "Autofill"
    base_bench: str = "Autofill"
    dev_options: str = "Threads=1 Hash=1024"
    base_options: str = "Threads=1 Hash=1024"
    dev_network: str = ""
    base_network: str = ""
    dev_time_control: str | None = None
    base_time_control: str | None = None
    book_name: str = "NONE"
    upload_pgns: str = "FALSE"
    test_mode: str = "SPRT"
    test_bounds: str = "auto"
    test_confidence: str = "auto"
    test_max_games: int | Literal["auto"] = 0
    priority: int = 0
    throughput: int = 1000
    workload_size: int = 32
    syzygy_wdl: str = "DISABLED"
    syzygy_adj: str = "OPTIONAL"
    win_adj: str = "None"
    draw_adj: str = "None"
    scale_method: str = "BASE"
    scale_nps: int | Literal["auto"] = "auto"

    @field_validator(
        "dev_engine",
        "base_engine",
        "dev_repo",
        "base_repo",
        "dev_branch",
        "base_branch",
        "dev_time_control",
        "base_time_control",
        mode="before",
    )
    @classmethod
    def _strip_optional_str(cls, v: JsonValue | None) -> str | None:
        return _strip_optional_string_value(v)

    @field_validator(
        "dev_bench",
        "base_bench",
        "dev_options",
        "base_options",
        "dev_network",
        "base_network",
        "book_name",
        "upload_pgns",
        "test_mode",
        "test_bounds",
        "test_confidence",
        "syzygy_wdl",
        "syzygy_adj",
        "win_adj",
        "draw_adj",
        "scale_method",
        mode="before",
    )
    @classmethod
    def _strip_required_str(cls, v: JsonValue | None) -> str:
        return str(v).strip()

    @field_validator("test_max_games", mode="before")
    @classmethod
    def _normalize_test_max_games(cls, v: JsonValue | None) -> int | Literal["auto"]:
        if isinstance(v, str):
            stripped = v.strip() or "0"
            if stripped == "auto":
                return "auto"
            if (parsed := coerce_int(stripped)) is None:
                raise ValueError("test_max_games must be an integer or 'auto'")
            return parsed
        if (parsed := coerce_int(v)) is None:
            raise ValueError("test_max_games must be an integer or 'auto'")
        return parsed

    @field_validator("scale_nps", mode="before")
    @classmethod
    def _normalize_scale_nps(cls, v: JsonValue | None) -> int | Literal["auto"]:
        if isinstance(v, str):
            stripped = v.strip() or "auto"
            if stripped == "auto":
                return "auto"
            if (parsed := coerce_int(stripped)) is None:
                raise ValueError("scale_nps must be an integer or 'auto'")
            return parsed
        if (parsed := coerce_int(v)) is None:
            raise ValueError("scale_nps must be an integer or 'auto'")
        return parsed


class OpenBenchCreateConfig(BaseModel):
    """OpenBench/ShogiBench create-test settings."""

    discovery_timeout_sec: float = Field(default=180.0, gt=0)
    payload: OpenBenchCreatePayload = Field(default_factory=OpenBenchCreatePayload)


class OpenBenchConfig(BaseModel):
    """OpenBench/ShogiBench submission configuration."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    is_enabled: bool = Field(default=False, alias="enabled")
    mode: Literal["existing_test", "create_test"] = "existing_test"
    server: str | None = None
    username: str | None = None
    password_env: str = "OPENBENCH_PASSWORD"
    target_test_id: int | None = Field(default=None, gt=0)
    submit_interval_games: int = Field(default=2, gt=0)
    is_strict: bool = Field(default=True, alias="strict")
    heartbeat_interval_sec: float = Field(default=30.0, gt=0)
    poll_interval_sec: float = Field(default=5.0, gt=0)
    assignment_timeout_sec: float = Field(default=120.0, gt=0)
    is_insecure_http_allowed: bool = Field(default=False, alias="allow_insecure_http")
    create: OpenBenchCreateConfig | None = None

    @field_validator("server", "username", mode="before")
    @classmethod
    def _strip_optional_identity(cls, v: JsonValue | None) -> str | None:
        return _strip_optional_string_value(v)

    @field_validator("password_env", mode="before")
    @classmethod
    def _strip_password_env(cls, v: JsonValue | None) -> str:
        return str(v or "").strip() or "OPENBENCH_PASSWORD"

    @model_validator(mode="after")
    def _validate_openbench(self) -> Self:
        if self.server:
            parsed = urlparse(self.server)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("openbench.server must start with http:// or https://")
            if not parsed.netloc:
                raise ValueError("openbench.server must include host")
            if parsed.scheme != "https" and not self.is_insecure_http_allowed:
                raise ValueError("openbench.server must use https:// unless allow_insecure_http=true")

        if self.is_enabled:
            if not self.server:
                raise ValueError("openbench.server is required when openbench.enabled=true")
            if not self.username:
                raise ValueError("openbench.username is required when openbench.enabled=true")
            if not self.password_env:
                raise ValueError("openbench.password_env is required when openbench.enabled=true")
            if self.mode == "existing_test":
                if self.target_test_id is None:
                    raise ValueError(
                        "openbench.target_test_id is required when openbench.enabled=true and mode=existing_test"
                    )
            if self.mode == "create_test" and self.create is None:
                raise ValueError("openbench.create is required when openbench.mode=create_test")
        return self


_ConfigPayloadValue = JsonValue | Path | tuple[Path, ...] | list[JsonObject]
_ConfigPayload = dict[str, _ConfigPayloadValue]
_InstanceSourceInput = str | Path | list[str | Path] | tuple[str | Path, ...] | set[str | Path] | None


def _coerce_instance_sources_input(value: object, *, field_name: str) -> _InstanceSourceInput:
    match value:
        case None:
            return None
        case str() | Path() as path_like:
            return path_like
        case list() | tuple() | set() as collection:
            normalized: list[str | Path] = []
            for item in collection:
                if isinstance(item, str | Path):
                    normalized.append(item)
                    continue
                raise TypeError(f"{field_name} entries must be str or Path values")
            return normalized
        case _:
            raise TypeError(f"{field_name} must be a string/path, list, tuple, or set")
