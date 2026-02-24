"""Shared configuration models and small helpers for arena configs.

This module contains Pydantic models used across tournament and SPSA
configuration flows. Parsing/validation of YAML is handled by higher-level
loaders; here we focus on modeling and small utilities with strict and
transparent error handling.
"""

import logging
import random
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator
from rshogi.core import parse_usi_position
from typing_extensions import Self

from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.types.types import STARTING_SFEN

logger = logging.getLogger(__name__)


class InitialPositionConfig(BaseModel):
    """Configuration for initial position generation."""

    type: Literal["startpos", "file"] = "startpos"
    flip_policy: Literal["alternate", "random", "none", "pair_both"] = "pair_both"
    source: str | None = None  # File path for type="file"

    def generate(self, num_positions: int, seed: str) -> list[str]:
        """Generate initial positions based on configuration.

        - type == "startpos": returns the standard startposition repeatedly.
        - type == "file": reads positions from ``source`` and normalizes each line
          to a raw SFEN (expands ``startpos + moves`` using the parser). Lines that
          fail to normalize are kept as-is, with a debug log for triage.

        Raises:
            FileNotFoundError: when ``source`` file is missing (type == file)
            OSError: on other file I/O errors (type == file)
            UnicodeDecodeError: if file cannot be decoded as UTF-8 (type == file)
        """
        if self.type == "startpos":
            return self.generate_startpos(num_positions)
        elif self.type == "file" and self.source:
            # Load from file and cycle/sample as needed
            with open(self.source, encoding="utf-8") as f:
                raw_lines = [line.strip() for line in f if line.strip()]

            # Normalize lines to raw SFEN (strip prefixes, expand startpos+moves)
            positions: list[str] = []
            for line in raw_lines:
                # Fast-path: raw SFEN (looks like "... b - 1")
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
                    # Use parser to apply moves then export canonical SFEN
                    b = parse_usi_position(line)
                    positions.append(b.to_sfen())
                    continue
                # Otherwise try to parse; if it errors, keep as-is
                # Accept raw SFEN (no prefix) directly
                parts = line.split()
                if len(parts) >= 3 and "/" in parts[0]:
                    positions.append(line)
                    continue
                # Fallback: parser (may raise ValueError or similar)
                b = parse_usi_position(line)
                positions.append(b.to_sfen())

            rng = random.Random(seed)
            return [rng.choice(positions) for _ in range(num_positions)]
        else:
            # Fallback with warning
            logger.warning(
                "Unknown initial position type '%s' - falling back to startpos. Use 'startpos' or 'file'.",
                self.type,
            )
            return self.generate_startpos(num_positions)

    def generate_startpos(self, num_positions: int) -> list[str]:
        """Generate standard starting positions.

        Returns a list consisting of the canonical "startpos" SFEN equivalent.
        """
        return [STARTING_SFEN] * num_positions


class AdjudicationSettings(BaseModel):
    """Adjudication knobs controlling resign and max-move policies."""

    resign_threshold_cp: int | None = Field(default=None, gt=0)
    resign_move_count: int = 8
    resign_two_sided: bool = True

    enable_max_plies: bool = True
    max_plies: int | None = Field(default=320, gt=0)
    sync_max_plies_with_engine: bool = True
    engine_max_ply_option_names: str | list[str] = "auto"

    @field_validator("engine_max_ply_option_names", mode="wrap")
    @classmethod
    def _normalize_option_names(cls, v: Any, handler: Any) -> list[str]:
        # Single string → wrap in list (e.g., "auto" → ["auto"])
        if isinstance(v, str):
            trimmed = v.strip()
            return [trimmed] if trimmed else []
        # Let Pydantic validate as list[str], then deduplicate and strip
        result = handler(v)
        deduped: list[str] = []
        seen: set[str] = set()
        for n in result:
            val = str(n).strip()
            if val and val not in seen:
                seen.add(val)
                deduped.append(val)
        return deduped

    @model_validator(mode="after")
    def _validate_max_plies_consistency(self) -> Self:
        if self.enable_max_plies:
            if self.max_plies is None:
                raise ValueError("adjudication.max_plies must be set when enable_max_plies is true")
        else:
            self.max_plies = None
        return self


class RulesConfig(BaseModel):
    """Game rules configuration."""

    # Optional time control settings nested under rules
    time_control: TimeControlLimits | None = None
    # Initial positions (migrated from TournamentConfig)
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
    def _strip_optional_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip() or None

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
    def _strip_required_str(cls, v: Any) -> str:
        return str(v).strip()

    @field_validator("test_max_games", mode="before")
    @classmethod
    def _normalize_test_max_games(cls, v: Any) -> int | Literal["auto"]:
        if isinstance(v, str):
            stripped = v.strip() or "0"
            return stripped if stripped == "auto" else int(stripped)
        return int(v)

    @field_validator("scale_nps", mode="before")
    @classmethod
    def _normalize_scale_nps(cls, v: Any) -> int | Literal["auto"]:
        if isinstance(v, str):
            stripped = v.strip() or "auto"
            return stripped if stripped == "auto" else int(stripped)
        return int(v)


class OpenBenchCreateConfig(BaseModel):
    """OpenBench/ShogiBench create-test settings."""

    discovery_timeout_sec: float = Field(default=180.0, gt=0)
    payload: OpenBenchCreatePayload = Field(default_factory=OpenBenchCreatePayload)


class OpenBenchConfig(BaseModel):
    """OpenBench/ShogiBench submission configuration."""

    enabled: bool = False
    mode: Literal["existing_test", "create_test"] = "existing_test"
    server: str | None = None
    username: str | None = None
    password_env: str = "OPENBENCH_PASSWORD"
    target_test_id: int | None = Field(default=None, gt=0)
    submit_interval_games: int = Field(default=2, gt=0)
    strict: bool = True
    heartbeat_interval_sec: float = Field(default=30.0, gt=0)
    poll_interval_sec: float = Field(default=5.0, gt=0)
    assignment_timeout_sec: float = Field(default=120.0, gt=0)
    allow_insecure_http: bool = False
    create: OpenBenchCreateConfig | None = None

    @field_validator("server", mode="before")
    @classmethod
    def _strip_server(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip() or None

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip() or None

    @field_validator("password_env", mode="before")
    @classmethod
    def _strip_password_env(cls, v: Any) -> str:
        return str(v or "").strip() or "OPENBENCH_PASSWORD"

    @model_validator(mode="after")
    def _validate_openbench(self) -> Self:
        if self.server:
            parsed = urlparse(self.server)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("openbench.server must start with http:// or https://")
            if not parsed.netloc:
                raise ValueError("openbench.server must include host")
            if parsed.scheme != "https" and not self.allow_insecure_http:
                raise ValueError("openbench.server must use https:// unless allow_insecure_http=true")

        if self.enabled:
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
