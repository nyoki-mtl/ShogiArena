"""Tournament configuration models (concrete) + shared exports.

This module defines tournament-specific configs (EngineConfig, TournamentRunConfig, etc.).
Common/shared pieces (InitialPositionConfig, RulesConfig, helper utils) live in
`shogiarena.configs.base` and are imported here to provide a cohesive API.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self

from shogiarena.arena.configs.base import (
    InitialPositionConfig,
    OpenBenchConfig,
    RulesConfig,
    SprtConfig,
)
from shogiarena.arena.configs.errors import ConfigError
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.types.coerce import coerce_str, coerce_str_list
from shogiarena.utils.types.types import JsonObject

logger = logging.getLogger(__name__)


def _parse_cpu_affinity_spec(value: str | int | Iterable[object] | None) -> tuple[int, ...]:
    """Parse cpu_affinity specification into a normalized tuple of CPU ids."""

    def _ensure_int(token: str | int | float | bool) -> int:
        try:
            parsed = int(token)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cpu_affinity entries must be integers or ranges; got '{token}'") from exc
        if parsed < 0:
            raise ValueError(f"cpu_affinity entries must be >= 0; got {parsed}")
        return parsed

    def _expand_range(token: str) -> Iterable[int]:
        parts = token.split("-", 1)
        if len(parts) != 2:
            return (_ensure_int(token),)
        start = _ensure_int(parts[0])
        end = _ensure_int(parts[1])
        if end < start:
            raise ValueError(f"cpu_affinity range must be ascending; got '{token}'")
        return range(start, end + 1)

    match value:
        case None:
            return ()
        case str() as s:
            tokens = [t.strip() for t in s.split(",") if t.strip()]
            if not tokens:
                return ()
            expanded: list[int] = []
            for token in tokens:
                expanded.extend(_expand_range(token))
        case Iterable() as items:
            expanded = []
            for item in items:
                match item:
                    case str() as s:
                        expanded.extend(_expand_range(s.strip()))
                    case int() | float() | bool():
                        expanded.append(_ensure_int(item))
                    case _:
                        raise ValueError(f"cpu_affinity entries must be integers or ranges; got '{item}'")
        case _:
            raise TypeError(
                f"cpu_affinity must be specified as a string or iterable of ints; got {type(value).__name__}"
            )
    seen: set[int] = set()
    normalized: list[int] = []
    for cpu_id in expanded:
        if cpu_id not in seen:
            normalized.append(cpu_id)
            seen.add(cpu_id)
    return tuple(normalized)


def _normalize_for_hash(value: object) -> object:
    match value:
        case BaseModel() as model:
            return _normalize_for_hash(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return _normalize_for_hash(asdict(dc))
        case Path() as p:
            return str(p)
        case dict() as d:
            return {str(k): _normalize_for_hash(v) for k, v in sorted(d.items(), key=lambda item: str(item[0]))}
        case list() | tuple() | set() as seq:
            return [_normalize_for_hash(v) for v in seq]
        case _:
            return value


def _hash_engine_config(spec: EngineConfig, *, length: int = 8) -> str:
    payload = {
        "engine_path": spec.engine_path,
        "artifact": spec.artifact,
        "build_options": spec.build_options,
        "options": spec.options,
        "options_overlays": spec.options_overlays,
        "mate_default_ply_limit": spec.mate_default_ply_limit,
        "mate_default_node_limit": spec.mate_default_node_limit,
        "mate_default_infinite": spec.mate_default_infinite,
        "mate_wait_for_bestmove": spec.mate_wait_for_bestmove,
        "isready_sync_strategy": spec.isready_sync_strategy,
        "time_control": spec.time_control,
        "instance_id": spec.instance_id,
        "cpu_affinity": spec.cpu_affinity,
        "name_style": spec.name_style,
    }
    normalized = _normalize_for_hash(payload)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


class EngineConfig(BaseModel):
    """Configuration for a single engine in the tournament."""

    engine_path: Path | None = None
    artifact: str | None = None
    build_options: dict[str, object] = Field(default_factory=dict)
    name: str | None = None
    name_style: Literal["hash", "short"] = "hash"
    options: dict[str, object] = Field(default_factory=dict)
    options_overlays: list[Path] = Field(default_factory=list)
    mate_default_ply_limit: int | None = Field(default=None, gt=0)
    mate_default_node_limit: int | None = Field(default=None, gt=0)
    mate_default_infinite: bool = False
    mate_wait_for_bestmove: bool = False
    isready_sync_strategy: Literal["direct", "wait", "stop"] = "direct"
    isready_lock_key: str | None = None
    isready_lock_template: str | None = None
    isready_lock_check_key: str | None = None
    isready_lock_check_template: str | None = None
    isready_lock_check_templates: tuple[str, ...] = ()
    isready_lock_skip_if_exists: bool = False
    handshake_timeout: float | None = Field(default=None, gt=0)
    time_control: TimeControlLimits | None = None
    instance_id: str | None = None
    cpu_affinity: tuple[int, ...] | None = None

    @field_validator("engine_path", mode="before")
    @classmethod
    def _resolve_engine_path(cls, v: Any) -> Path | None:
        if v is None:
            return None
        return Path(resolve_path_like(str(v)))

    @field_validator("options_overlays", mode="before")
    @classmethod
    def _normalize_options_overlays(cls, v: Any) -> list[Path]:
        if v is None or (isinstance(v, list | tuple) and not v):
            return []
        normalized: list[Path] = []
        items = v if isinstance(v, list | tuple) else [v]
        for raw in items:
            candidate = Path(resolve_path_like(str(raw)))
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
        return v.strip() or None

    @field_validator("isready_lock_check_templates", mode="wrap")
    @classmethod
    def _normalize_check_templates(cls, v: Any, handler: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        result = handler(v)
        return tuple(t for t in (s.strip() for s in result) if t)

    @field_validator("cpu_affinity", mode="before")
    @classmethod
    def _normalize_cpu_affinity(cls, v: Any) -> tuple[int, ...] | None:
        if v is None:
            return None
        parsed = _parse_cpu_affinity_spec(v)
        return parsed or None

    @model_validator(mode="after")
    def _validate_engine_config(self) -> Self:
        # Mate defaults: at most one
        if self.mate_default_ply_limit is not None and self.mate_default_node_limit is not None:
            raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
        if self.mate_default_infinite and (
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
            self.name = f"{base_name}@{_hash_engine_config(self, length=hash_len)}"

        return self

    def load_overlay_options(self) -> dict[str, object]:
        merged: dict[str, object] = {}
        for overlay in self.options_overlays:
            if not overlay.exists():
                raise FileNotFoundError(f"Options overlay file not found: {overlay}")
            with open(overlay, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, Mapping):
                raise TypeError("options_overlays YAML must be a mapping")
            opts = raw.get("options") if "options" in raw else raw
            if isinstance(opts, Mapping):
                merged.update(opts)
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
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    api_port: int = 8080


class SystemConfig(BaseModel):
    """System-level configuration placeholder."""

    resource_poll_interval: float = Field(default=0.1, gt=0)
    resource_poll_max_interval: float = Field(default=1.0, gt=0)
    engine_handshake_timeout: float | None = Field(default=None, gt=0)
    extras: dict[str, object] = Field(default_factory=dict)

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


class TournamentRunConfig(BaseModel):
    experiment_name: str
    engines: list[EngineConfig]
    tournament: TournamentConfig = Field(default_factory=TournamentConfig)
    generate: GenerateConfig | None = None
    rules: RulesConfig = Field(default_factory=RulesConfig)
    sprt: SprtConfig | None = None
    openbench: OpenBenchConfig | None = None
    rating: RatingConfig = Field(default_factory=RatingConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    log_level: str = "INFO"
    system: SystemConfig = Field(default_factory=SystemConfig)
    records_output: RecordOutputConfig | None = None

    instances: tuple[Path, ...] | None = None
    output_dir: Path = Field(default_factory=lambda: project_dirs.output_dir)
    source_path: Path | None = None

    @field_validator("system", mode="before")
    @classmethod
    def _normalize_system(cls, v: Any) -> Any:
        """Handle extras passthrough for SystemConfig."""
        if isinstance(v, Mapping):
            allowed_keys = {
                "resource_poll_interval",
                "resource_poll_max_interval",
                "engine_handshake_timeout",
                "extras",
            }
            payload = {k: v[k] for k in v.keys() if k in allowed_keys}
            extras = {k: v[k] for k in v.keys() if k not in allowed_keys}
            if extras:
                payload["extras"] = extras
            return payload
        return v

    @model_validator(mode="after")
    def _validate_tournament_run(self) -> Self:
        generate_mode = self.generate is not None or str(self.experiment_name or "").strip().lower() == "generate"

        if generate_mode:
            if len(self.engines) != 1:
                raise ValueError("Generate requires exactly 1 engine")
        elif len(self.engines) < 2:
            raise ValueError("At least 2 engines required for tournament")

        # Engine name uniqueness
        engine_names = [str(e.name) for e in self.engines]
        if len(engine_names) != len(set(engine_names)):
            raise ValueError("Engine names must be unique")

        # Validate each engine has exactly one of artifact or engine_path
        for e in self.engines:
            has_cfg = e.engine_path is not None
            has_art = e.artifact is not None and e.artifact.strip() != ""
            if has_cfg == has_art:
                raise ValueError(f"Engine '{e.name}': specify exactly one of 'artifact' or 'engine_path'")
            if has_cfg:
                assert e.engine_path is not None  # has_cfg guarantees this
                if not e.engine_path.exists():
                    raise FileNotFoundError(f"Engine config file not found: {e.engine_path}")
                bo = e.build_options or {}
                if bo:
                    raise ValueError(f"Engine '{e.name}': build_options must not be set when using engine_path")
            if has_art:
                art = str(e.artifact).strip()
                if not re.match(r"^[A-Za-z0-9._-]+/[A-Fa-f0-9]{6,40}$", art):
                    raise ValueError(f"Engine '{e.name}': artifact must be '<repo>/<commit_hash>' (hex)")

        # Generate mode setup
        if generate_mode:
            if self.sprt is not None:
                raise ValueError("Generate mode does not support sprt")
            if self.generate is None:
                self.generate = GenerateConfig(
                    games=int(self.tournament.games_per_pair),
                    seed=int(self.tournament.seed),
                    num_parallel=int(self.tournament.num_parallel),
                )
            self.tournament.scheduler = "selfplay"
            self.tournament.games_per_pair = int(self.generate.games)
            self.tournament.seed = int(self.generate.seed)
            self.tournament.num_parallel = int(self.generate.num_parallel)

        # SPRT constraints
        if self.sprt is not None and len(self.engines) != 2:
            raise ValueError("SPRT requires exactly two engines")
        if self.openbench is not None and self.openbench.enabled and self.sprt is None:
            raise ValueError("openbench.enabled=true requires sprt configuration")
        if self.sprt is not None:
            if self.sprt.max_games is not None and self.tournament.games_per_pair == 4:
                self.tournament.games_per_pair = int(self.sprt.max_games)
            if self.sprt.num_parallel is not None and self.tournament.num_parallel == 4:
                self.tournament.num_parallel = int(self.sprt.num_parallel)

        # Normalize instances
        if self.instances:
            normalized: list[Path] = []
            seen: set[Path] = set()
            for entry in self.instances:
                p = Path(entry)
                if not p.is_absolute():
                    p = p.resolve()
                if p in seen:
                    raise ValueError(f"Duplicate instance source specified: {p}")
                seen.add(p)
                normalized.append(p)
            self.instances = tuple(normalized)
        else:
            self.instances = None

        self.output_dir = Path(self.output_dir)
        return self

    @classmethod
    def from_yaml(cls, yaml_path: Path | str) -> TournamentRunConfig:
        yaml_path = Path(yaml_path).resolve()
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            location = ""
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                line = getattr(mark, "line", None)
                column = getattr(mark, "column", None)
                if line is not None and column is not None:
                    location = f":{line + 1}:{column + 1}"
            raise ConfigError(f"Failed to parse YAML {yaml_path}{location}: {exc}") from exc
        try:
            return cls.from_mapping(data, base_dir=yaml_path.parent, source_path=yaml_path)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid tournament config {yaml_path}: {exc}") from exc

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        base_dir: Path | None = None,
        source_path: Path | None = None,
    ) -> TournamentRunConfig:
        if not isinstance(data, Mapping):
            raise TypeError("TournamentRunConfig data must be a mapping at top-level")
        if "generate" in data and "tournament" in data:
            raise ValueError("Generate config must not include tournament section")
        if "run_dir" in data:
            raise ValueError("TournamentRunConfig.run_dir is deprecated; supply a RunStorage instead")
        allowed = set(cls.model_fields.keys())
        extras = sorted(k for k in data.keys() if k not in allowed)
        if extras:
            logger.warning("Unknown keys in TournamentRunConfig data: %s", ", ".join(extras))
        payload: dict[str, Any] = {k: data[k] for k in data.keys() if k in allowed}
        if "output_dir" not in payload:
            payload["output_dir"] = project_dirs.output_dir
        if not payload.get("experiment_name"):
            if source_path is not None:
                p = Path(source_path)
                parent_name = p.parent.name
                payload["experiment_name"] = p.stem if parent_name in {"arena", "spsa"} else parent_name
            else:
                payload["experiment_name"] = "arena"
        output_dir_path = Path(payload["output_dir"])
        payload["output_dir"] = output_dir_path
        records_output_raw = payload.get("records_output")
        if isinstance(records_output_raw, Mapping):
            output_raw = coerce_str(records_output_raw.get("output_dir"))
            if output_raw is not None:
                base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
                candidate = Path(resolve_path_like(output_raw))
                if not candidate.is_absolute():
                    candidate = (base / candidate).resolve()
                records_output_raw["output_dir"] = candidate
        instances_raw = payload.get("instances", None)
        if instances_raw is not None:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            normalized_instances = cls._resolve_instance_sources(instances_raw, base_dir=base)
            payload["instances"] = tuple(normalized_instances) if normalized_instances else None
        engines_raw = payload.get("engines")
        if isinstance(engines_raw, list) and engines_raw:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            for engine in engines_raw:
                if not isinstance(engine, Mapping):
                    continue
                raw_overlays = engine.get("options_overlays")
                if raw_overlays is None:
                    continue
                items = coerce_str_list(raw_overlays, field="options_overlays")
                overlay_paths: list[str] = []
                for item in items:
                    candidate = Path(resolve_path_like(item))
                    if not candidate.is_absolute():
                        candidate = (base / candidate).resolve()
                    overlay_paths.append(str(candidate))
                engine["options_overlays"] = overlay_paths
        rconf = cast(dict[str, Any], payload.get("rules") or {})
        ipos = cast(dict[str, Any], rconf.get("initial_positions") or {})
        source = ipos.get("source")
        if isinstance(source, str) and source.strip():
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            resolved = cls._resolve_initial_source(source, base_dir=base)
            ipos["source"] = resolved
            rconf["initial_positions"] = ipos
            payload["rules"] = rconf
        cfg = cls.model_validate(payload)
        cfg.source_path = source_path
        return cfg

    @staticmethod
    def _resolve_initial_source(path_str: str, base_dir: Path) -> str:
        raw = resolve_path_like(path_str)
        p = Path(raw)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        return str(p)

    @staticmethod
    def _resolve_instance_sources(
        raw: str | Path | list[str | Path] | tuple[str | Path, ...] | set[str | Path] | None,
        *,
        base_dir: Path,
    ) -> tuple[Path, ...]:
        """Normalize ``instances`` entries into absolute ``Path`` objects."""

        match raw:
            case None:
                return ()
            case str() | Path() as single:
                items = [single]
            case list() | tuple() | set() as collection:
                items = list(collection)
            case _:
                raise TypeError("instances must be a string path or a list of string paths")

        resolved: list[Path] = []
        seen: set[Path] = set()
        for item in items:
            match item:
                case Path() as p:
                    candidate = p
                case str() as s:
                    candidate = Path(resolve_path_like(s))
                case _:
                    raise TypeError("instances entries must be str or Path values")
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if candidate in seen:
                raise ValueError(f"Duplicate instances path specified: {candidate}")
            seen.add(candidate)
            resolved.append(candidate)
        return tuple(resolved)

    def get_schedule_hash(self) -> str:
        components = [
            self.tournament.seed or "",
            str(self.tournament.games_per_pair),
            self.tournament.scheduler,
            "|".join(str(e.name) for e in self.engines),
            self.tournament.game_order,
            getattr(self.rules.initial_positions, "flip_policy", None) or "",
            getattr(self.tournament, "baseline_count", 1),
        ]
        hash_input = "-".join(str(c) for c in components)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


@dataclass
class GameSpec:
    black_engine: str
    white_engine: str
    initial_sfen: str
    game_id: str
    round_num: int = 0
    # Preferred instances for each side when available; None falls back to scheduler defaults
    assigned_instance_black: str | None = None
    assigned_instance_white: str | None = None
    # When True, remote instances should ensure the arena environment is prepared before running
    require_install: bool = False

    @classmethod
    def create(cls, black: str, white: str, sfen: str, round_num: int, seed: str, version: str = "v1") -> GameSpec:
        def normalize(name: str) -> str:
            return re.sub(r"[^a-z0-9]", "", name.lower())

        def round_token(value: int) -> str:
            if value < 0:
                raise ValueError("round_num must be non-negative")
            # Round numbers are now represented as one-based zero-padded decimal strings to
            # keep the table ordering intuitive for operators (g0009 -> g0010) and align
            # dashboard numbering with user-facing order (first game -> g0001).
            normalized = value + 1
            return str(normalized).rjust(4, "0")

        def short_digest(*components: str, length: int = 10) -> str:
            payload = "|".join(components)
            digest = hashlib.blake2s(payload.encode(), digest_size=6).digest()
            token = base64.b32encode(digest).decode("ascii").rstrip("=")
            return token.lower()[:length]

        black_norm = normalize(black)
        white_norm = normalize(white)
        hash_components = [black_norm, white_norm, str(round_num), sfen, seed, version]
        pair_token = short_digest(*hash_components)
        game_id = f"g{round_token(round_num)}-{pair_token}"
        return cls(
            black_engine=black,
            white_engine=white,
            initial_sfen=sfen,
            game_id=game_id,
            round_num=round_num,
        )


class _WdlCounts(TypedDict):
    """WDL (勝敗引分) カウント。"""

    wins: int
    draws: int
    losses: int


class _EngineWdlCounts(_WdlCounts, total=False):
    """エンジンごとの WDL + ゲーム数。

    ``games`` は一部の集計パスで動的に追加されるためオプショナル。
    """

    games: int


@dataclass
class TournamentResults:
    engine_stats: dict[str, _EngineWdlCounts]
    pair_results: dict[tuple[str, str], dict[str, int]]
    completed_games: list[str]
    total_games: int
    completed_games_count: int
    cancelled_games_count: int = 0

    def get_leaderboard(self) -> list[JsonObject]:
        leaderboard: list[JsonObject] = []
        for engine, stats in self.engine_stats.items():
            points = stats["wins"] + 0.5 * stats["draws"]
            games = stats.get("games")
            if games is None:
                games = stats["wins"] + stats["losses"] + stats["draws"]
            win_rate = stats["wins"] / games if games > 0 else 0
            leaderboard.append(
                {
                    "rank": 0,
                    "engine": engine,
                    "points": points,
                    "games": games,
                    "wins": stats["wins"],
                    "draws": stats["draws"],
                    "losses": stats["losses"],
                    "win_rate": win_rate,
                }
            )

        def _sort_key(x: JsonObject) -> tuple[float, float]:
            return (-float(cast(float, x["points"])), -float(cast(float, x["win_rate"])))

        leaderboard.sort(key=_sort_key)
        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i
        return leaderboard


__all__ = [
    "TournamentRunConfig",
    "RulesConfig",
    "TournamentConfig",
    "GenerateConfig",
    "OpenBenchConfig",
    "DashboardConfig",
    "SystemConfig",
    "RecordOutputConfig",
    "GameSpec",
    "EngineConfig",
    "InitialPositionConfig",
    "TournamentResults",
]
