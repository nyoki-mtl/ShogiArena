"""Tournament configuration models (concrete) + shared exports.

This module defines tournament-specific configs (EngineSpec, ArenaConfig, etc.).
Common/shared pieces (InitialPositions, RulesConfig, helper utils) live in
`shogiarena.configs.base` and are imported here to provide a cohesive API.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from shogiarena.arena.configs.base import (
    InitialPositions,
    RulesConfig,
    SprtConfig,
)
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.run_paths import default_run_dir, timestamp_slug

logger = logging.getLogger(__name__)


def _parse_cpu_affinity_spec(value: Any) -> tuple[int, ...]:
    """Parse cpu_affinity specification into a normalized tuple of CPU ids."""

    def _ensure_int(token: Any) -> int:
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

    if value is None:
        return ()
    if isinstance(value, str):
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        if not tokens:
            return ()
        expanded: list[int] = []
        for token in tokens:
            expanded.extend(_expand_range(token))
    elif isinstance(value, Iterable):
        expanded = []
        for item in value:
            if isinstance(item, str):
                expanded.extend(_expand_range(item.strip()))
            else:
                expanded.append(_ensure_int(item))
    else:
        raise TypeError(f"cpu_affinity must be specified as a string or iterable of ints; got {type(value).__name__}")
    seen: set[int] = set()
    normalized: list[int] = []
    for cpu_id in expanded:
        if cpu_id not in seen:
            normalized.append(cpu_id)
            seen.add(cpu_id)
    return tuple(normalized)


def _normalize_for_hash(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_for_hash(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _normalize_for_hash(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list | tuple | set):
        return [_normalize_for_hash(v) for v in value]
    return value


def _hash_engine_spec(spec: EngineSpec) -> str:
    payload = {
        "engine_config": spec.engine_config,
        "artifact": spec.artifact,
        "build_options": spec.build_options,
        "options": spec.options,
        "options_overlays": spec.options_overlays,
        "time_control": spec.time_control,
        "instance_id": spec.instance_id,
        "cpu_affinity": spec.cpu_affinity,
    }
    normalized = _normalize_for_hash(payload)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


@dataclass
class EngineSpec:
    """Specification for a single engine in the tournament."""

    engine_config: Path | None = None
    artifact: str | None = None
    build_options: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    options_overlays: list[Path] = field(default_factory=list)
    time_control: TimeControlLimits | None = None
    instance_id: str | None = None
    cpu_affinity: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.build_options, dict):
            raise TypeError("build_options must be a mapping")
        else:
            self.build_options = dict(self.build_options)
        if self.engine_config is not None:
            engine_cfg_str = str(self.engine_config)
            resolved_engine_cfg = resolve_path_like(engine_cfg_str)
            self.engine_config = Path(resolved_engine_cfg)
        if self.options_overlays is None:
            self.options_overlays = []
        if self.options_overlays:
            normalized: list[Path] = []
            for raw in self.options_overlays:
                candidate = Path(resolve_path_like(str(raw)))
                if not candidate.is_absolute():
                    candidate = candidate.resolve()
                if not candidate.exists():
                    raise FileNotFoundError(f"Options overlay file not found: {candidate}")
                normalized.append(candidate)
            self.options_overlays = normalized
        if isinstance(self.time_control, dict):
            # Let TypeError surface for invalid keys/types
            self.time_control = TimeControlLimits(**cast(dict[str, Any], self.time_control))
        if self.cpu_affinity is not None:
            parsed_affinity = _parse_cpu_affinity_spec(self.cpu_affinity)
            self.cpu_affinity = parsed_affinity or None
        if not self.name:
            if self.engine_config is not None:
                base_name = self.engine_config.name
            elif isinstance(self.artifact, str) and self.artifact.strip():
                base_name = self.artifact.strip()
            else:
                base_name = "engine"
            self.name = f"{base_name}@{_hash_engine_spec(self)}"

    def load_overlay_options(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for overlay in self.options_overlays:
            if not overlay.exists():
                raise FileNotFoundError(f"Options overlay file not found: {overlay}")
            with open(overlay, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, dict):
                raise TypeError("options_overlays YAML must be a mapping")
            opts = raw.get("options") if "options" in raw else raw
            if isinstance(opts, dict):
                merged.update(opts)
        return merged


@dataclass
class TournamentConfig:
    scheduler: str = "round_robin"
    games_per_pair: int = 4
    seed: int = 42
    num_parallel: int = 4
    game_order: Literal["auto", "pairwise", "interleave", "shuffle"] = "auto"
    baseline_count: int = 1

    def __post_init__(self) -> None:
        # Enforce integer seed; surface errors early
        if not isinstance(self.seed, int):
            raise TypeError(f"tournament.seed must be an integer: {self.seed}")


@dataclass
class RatingConfig:
    initial: float = 1500.0
    k_factor: float = 16.0


@dataclass
class DashboardConfig:
    enabled: bool = True
    api_port: int = 8080


@dataclass
class SystemConfig:
    """System-level configuration placeholder."""

    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.extras, dict):
            raise TypeError("system configuration must be a mapping when provided")


@dataclass
class ArenaConfig:
    experiment_name: str
    engines: list[EngineSpec]
    tournament: TournamentConfig = field(default_factory=TournamentConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    sprt: SprtConfig | None = None
    rating: RatingConfig = field(default_factory=RatingConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    log_level: str = "INFO"
    system: SystemConfig = field(default_factory=SystemConfig)

    instances: tuple[Path, ...] | None = None
    output_dir: Path = field(default_factory=lambda: project_dirs.output_dir)
    run_dir: Path | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if len(self.engines) < 2:
            raise ValueError("At least 2 engines required for tournament")
        self.engines = [e if isinstance(e, EngineSpec) else EngineSpec(**e) for e in self.engines]
        engine_names = [str(e.name) for e in self.engines]
        if len(engine_names) != len(set(engine_names)):
            raise ValueError("Engine names must be unique")
        sys_cfg = self.system
        if isinstance(sys_cfg, dict):
            self.system = SystemConfig(extras=cast(dict[str, Any], sys_cfg))
        for e in self.engines:
            has_cfg = getattr(e, "engine_config", None) is not None
            has_art = isinstance(getattr(e, "artifact", None), str) and str(e.artifact).strip() != ""
            if has_cfg == has_art:
                raise ValueError(f"Engine '{e.name}': specify exactly one of 'artifact' or 'engine_config'")
            if has_cfg:
                if not e.engine_config.exists():  # type: ignore[union-attr]
                    raise FileNotFoundError(f"Engine config file not found: {e.engine_config}")
                bo = getattr(e, "build_options", {}) or {}
                if isinstance(bo, dict) and len(bo) > 0:
                    raise ValueError(f"Engine '{e.name}': build_options must not be set when using engine_config")
            if has_art:
                art = str(e.artifact).strip()
                if not re.match(r"^[A-Za-z0-9._-]+/[A-Fa-f0-9]{6,40}$", art):
                    raise ValueError(f"Engine '{e.name}': artifact must be '<repo>/<commit_hash>' (hex)")
                bo = getattr(e, "build_options", {}) or {}
                if not isinstance(bo, dict) or not str(bo.get("target_cpu", "")).strip():
                    raise ValueError(f"Engine '{e.name}': build_options.target_cpu is required when using artifact")
        if isinstance(self.tournament, dict):
            self.tournament = TournamentConfig(**cast(dict[str, Any], self.tournament))
        if isinstance(self.rules, dict):
            self.rules = RulesConfig(**cast(dict[str, Any], self.rules))
        if isinstance(self.rules.time_control, dict):
            self.rules.time_control = TimeControlLimits(**cast(dict[str, Any], self.rules.time_control))
        if isinstance(self.sprt, dict):
            self.sprt = SprtConfig(**cast(dict[str, Any], self.sprt))
        if self.sprt is not None and len(self.engines) != 2:
            raise ValueError("SPRT requires exactly two engines")
        if self.sprt is not None:
            if self.sprt.max_games is not None and self.tournament.games_per_pair == 4:
                self.tournament.games_per_pair = int(self.sprt.max_games)
            if self.sprt.num_parallel is not None and self.tournament.num_parallel == 4:
                self.tournament.num_parallel = int(self.sprt.num_parallel)
        if isinstance(self.rating, dict):
            self.rating = RatingConfig(**cast(dict[str, Any], self.rating))
        if isinstance(self.dashboard, dict):
            raw = self.dashboard
            allowed_keys = {"enabled", "api_port"}
            filtered = {k: raw[k] for k in raw.keys() & allowed_keys}
            self.dashboard = DashboardConfig(**cast(dict[str, Any], filtered))
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
        if self.run_dir is not None:
            self.run_dir = Path(self.run_dir)
        if self.run_dir is None:
            self.run_dir = self.output_dir / "tournament" / self.experiment_name / timestamp_slug()

    @classmethod
    def from_yaml(cls, yaml_path: Path | str) -> ArenaConfig:
        yaml_path = Path(yaml_path).resolve()
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        return cls.from_mapping(data, base_dir=yaml_path.parent, source_path=yaml_path)

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        base_dir: Path | None = None,
        source_path: Path | None = None,
    ) -> ArenaConfig:
        if not isinstance(data, Mapping):
            raise TypeError("ArenaConfig data must be a mapping at top-level")
        allowed = set(getattr(cls, "__dataclass_fields__", {}).keys())
        extras = sorted(k for k in data.keys() if k not in allowed)
        if extras:
            logger.warning("Unknown keys in ArenaConfig data: %s", ", ".join(extras))
        payload = {k: data[k] for k in data.keys() if k in allowed}
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
        if not payload.get("run_dir") and source_path is not None:
            payload["run_dir"] = default_run_dir(source_path, output_dir_path / "tournament")
        instances_raw = payload.get("instances", None)
        if instances_raw is not None:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            normalized_instances = cls._resolve_instance_sources(instances_raw, base_dir=base)
            payload["instances"] = tuple(normalized_instances) if normalized_instances else None
        engines_raw = payload.get("engines")
        if isinstance(engines_raw, list) and engines_raw:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            for engine in engines_raw:
                if not isinstance(engine, dict):
                    continue
                overlays = engine.get("options_overlays")
                if overlays is None:
                    continue
                if isinstance(overlays, str):
                    items = [overlays]
                elif isinstance(overlays, list | tuple):
                    items = list(overlays)
                else:
                    raise TypeError("options_overlays must be a string or list of strings")
                overlay_paths: list[str] = []
                for item in items:
                    if not isinstance(item, str) or not item.strip():
                        raise TypeError("options_overlays entries must be non-empty strings")
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
        cfg = cls(**payload)
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
    def _resolve_instance_sources(raw: Any, *, base_dir: Path) -> tuple[Path, ...]:
        """Normalize ``instances`` entries into absolute ``Path`` objects."""

        if raw is None:
            return ()
        if isinstance(raw, str | Path):
            items = [raw]
        elif isinstance(raw, list | tuple | set):
            items = list(raw)
        else:
            raise TypeError("instances must be a string path or a list of string paths")

        resolved: list[Path] = []
        seen: set[Path] = set()
        for item in items:
            if isinstance(item, Path):
                candidate = item
            elif isinstance(item, str):
                candidate = Path(resolve_path_like(item))
            else:
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


@dataclass
class TournamentResults:
    engine_stats: dict[str, dict[str, int]]
    pair_results: dict[tuple[str, str], dict[str, int]]
    completed_games: list[str]
    total_games: int
    completed_games_count: int
    cancelled_games_count: int = 0

    def get_leaderboard(self) -> list[dict[str, Any]]:
        leaderboard = []
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

        def _sort_key(x: dict[str, Any]) -> tuple[float, float]:
            return (-float(x["points"]), -float(x["win_rate"]))

        leaderboard.sort(key=_sort_key)
        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i
        return leaderboard


__all__ = [
    "ArenaConfig",
    "RulesConfig",
    "TournamentConfig",
    "DashboardConfig",
    "SystemConfig",
    "GameSpec",
    "EngineSpec",
    "InitialPositions",
    "TournamentResults",
]
