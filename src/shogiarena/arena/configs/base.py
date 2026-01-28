"""Shared configuration models and small helpers for arena configs.

This module contains lightweight dataclasses used across tournament and SPSA
configuration flows. Parsing/validation of YAML is handled by higher-level
loaders; here we focus on modeling and small utilities with strict and
transparent error handling.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import cshogi

from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.board import sfen_parser

logger = logging.getLogger(__name__)


@dataclass
class InitialPositions:
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
                    positions.append(cshogi.STARTING_SFEN)
                    continue
                if line.startswith("position startpos") or line.startswith("startpos "):
                    # Use parser to apply moves then export canonical SFEN
                    b = sfen_parser(line)
                    positions.append(b.sfen())
                    continue
                # Otherwise try to parse; if it errors, keep as-is
                # Accept raw SFEN (no prefix) directly
                parts = line.split()
                if len(parts) >= 3 and "/" in parts[0]:
                    positions.append(line)
                    continue
                # Fallback: parser (may raise ValueError or similar)
                b = sfen_parser(line)
                positions.append(b.sfen())

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
        return [cshogi.STARTING_SFEN] * num_positions


@dataclass
class AdjudicationSettings:
    """Adjudication knobs controlling resign and max-move policies."""

    enable_resign: bool = False
    resign_score_cp: int = 800
    resign_move_count: int = 8
    resign_two_sided: bool = True

    enable_max_plies: bool = True
    max_plies: int | None = 320
    sync_max_plies_with_engine: bool = True
    engine_max_ply_option_names: str | list[str] = "auto"

    def __post_init__(self) -> None:
        self.resign_score_cp = int(self.resign_score_cp)
        self.resign_move_count = int(self.resign_move_count)

        if self.enable_max_plies:
            if self.max_plies is None:
                raise ValueError("adjudication.max_plies must be set when enable_max_plies is true")
            try:
                self.max_plies = int(self.max_plies)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
                raise TypeError("adjudication.max_plies must be an integer") from exc
            if self.max_plies <= 0:
                raise ValueError("adjudication.max_plies must be positive")
        else:
            self.max_plies = None

        names = self.engine_max_ply_option_names
        if isinstance(names, str):
            trimmed = names.strip()
            self.engine_max_ply_option_names = trimmed or []
        elif isinstance(names, list):
            deduped: list[str] = []
            seen: set[str] = set()
            for n in names:
                if not isinstance(n, str):
                    raise TypeError("adjudication.engine_max_ply_option_names entries must be strings")
                val = n.strip()
                if val and val not in seen:
                    seen.add(val)
                    deduped.append(val)
            self.engine_max_ply_option_names = deduped
        else:
            raise TypeError("adjudication.engine_max_ply_option_names must be str or list[str]")


@dataclass
class RulesConfig:
    """Game rules configuration."""

    # Optional time control settings nested under rules
    time_control: "TimeControlLimits | None" = None
    # Initial positions (migrated from TournamentConfig)
    initial_positions: InitialPositions = field(default_factory=InitialPositions)
    adjudication: AdjudicationSettings | dict[str, Any] = field(default_factory=AdjudicationSettings)
    repetition_occurrences_to_draw: int = 2

    def __post_init__(self) -> None:
        """Normalize nested structures for rules.

        - Ensure initial_positions is an InitialPositions instance when provided as a mapping.
        """
        if isinstance(self.initial_positions, dict):
            self.initial_positions = InitialPositions(**cast(dict[str, Any], self.initial_positions))

        if isinstance(self.adjudication, dict):
            self.adjudication = AdjudicationSettings(**cast(dict[str, Any], self.adjudication))

        self.repetition_occurrences_to_draw = int(self.repetition_occurrences_to_draw)
        if self.repetition_occurrences_to_draw not in (2, 3, 4):
            raise ValueError("repetition_occurrences_to_draw must be 2, 3, or 4")


@dataclass
class SprtConfig:
    """SPRT early stopping configuration."""

    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    min_games: int = 0
    max_games: int | None = None
    num_parallel: int | None = None

    def __post_init__(self) -> None:
        self.min_games = int(self.min_games)
        if self.min_games < 0:
            raise ValueError("sprt.min_games must be >= 0")
        if self.max_games is not None:
            self.max_games = int(self.max_games)
            if self.max_games <= 0:
                raise ValueError("sprt.max_games must be > 0")
        if self.num_parallel is not None:
            self.num_parallel = int(self.num_parallel)
            if self.num_parallel <= 0:
                raise ValueError("sprt.num_parallel must be > 0")
