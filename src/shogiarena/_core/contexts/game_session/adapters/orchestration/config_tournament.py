"""Tournament run configuration model and mapping loader."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str, coerce_str_list
from shogiarena._core.shared.kernel.serialization import json_serialize

from .config_core import (
    OpenBenchConfig,
    RulesConfig,
    SprtConfig,
    _coerce_instance_sources_input,
    _ConfigPayload,
)
from .config_engine import (
    DashboardConfig,
    EngineConfig,
    GenerateConfig,
    RatingConfig,
    RecordOutputConfig,
    SystemConfig,
    TournamentConfig,
)

logger = logging.getLogger(__name__)


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
    def _normalize_system(
        cls,
        v: Mapping[str, JsonValue] | SystemConfig | None,
    ) -> dict[str, JsonValue] | SystemConfig | None:
        """Handle extras passthrough for SystemConfig."""
        if isinstance(v, Mapping):
            source = coerce_json_object_serialized(v, field_name="system")
            allowed_keys = {
                "resource_poll_interval",
                "resource_poll_max_interval",
                "engine_handshake_timeout",
                "extras",
            }
            payload: dict[str, JsonValue] = {k: source[k] for k in source if k in allowed_keys}
            extras: dict[str, JsonValue] = {k: source[k] for k in source if k not in allowed_keys}
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
        if self.openbench is not None and self.openbench.is_enabled and self.sprt is None:
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
    def from_mapping(
        cls,
        data: Mapping[str, JsonValue],
        *,
        base_dir: Path | None = None,
        source_path: Path | None = None,
    ) -> TournamentRunConfig:
        if not isinstance(data, Mapping):
            raise TypeError("TournamentRunConfig data must be a mapping at top-level")
        if data.get("generate") is not None and data.get("tournament") is not None:
            raise ValueError("Generate config must not include tournament section")
        allowed = set(cls.model_fields.keys())
        extras = sorted(k for k in data.keys() if k not in allowed)
        if extras:
            logger.warning("Unknown keys in TournamentRunConfig data: %s", ", ".join(extras))
        payload: _ConfigPayload = {str(k): data[k] for k in data.keys() if k in allowed}
        if "output_dir" not in payload:
            payload["output_dir"] = project_dirs.output_dir
        if not payload.get("experiment_name"):
            if source_path is not None:
                p = Path(source_path)
                parent_name = p.parent.name
                payload["experiment_name"] = p.stem if parent_name in {"arena", "spsa"} else parent_name
            else:
                payload["experiment_name"] = "arena"
        output_dir_value = payload.get("output_dir")
        if isinstance(output_dir_value, Path):
            output_dir_path = output_dir_value
        else:
            output_dir_str = coerce_str(json_serialize(output_dir_value))
            if output_dir_str is None:
                raise TypeError("output_dir must be a string or path-like value")
            output_dir_path = Path(output_dir_str)
        payload["output_dir"] = output_dir_path
        records_output_raw = payload.get("records_output")
        if isinstance(records_output_raw, Mapping):
            records_output_map = {str(key): value for key, value in records_output_raw.items()}
            output_raw = coerce_str(json_serialize(records_output_map.get("output_dir")))
            if output_raw is not None:
                base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
                candidate = Path(resolve_path_like(output_raw))
                if not candidate.is_absolute():
                    candidate = (base / candidate).resolve()
                records_output_map["output_dir"] = candidate
            payload["records_output"] = records_output_map
        instances_raw = payload.get("instances", None)
        if instances_raw is not None:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            parsed_instances_raw = _coerce_instance_sources_input(instances_raw, field_name="instances")
            normalized_instances = cls._resolve_instance_sources(
                parsed_instances_raw,
                base_dir=base,
            )
            payload["instances"] = tuple(normalized_instances) if normalized_instances else None
        engines_raw = payload.get("engines")
        if isinstance(engines_raw, list) and engines_raw:
            base = base_dir or (Path(source_path).parent if source_path is not None else Path.cwd())
            normalized_engines: list[JsonObject] = []
            for engine in engines_raw:
                if not isinstance(engine, Mapping):
                    continue
                engine_map = coerce_json_object_serialized(engine, field_name="engines[]")
                raw_overlays = engine_map.get("options_overlays")
                if raw_overlays is None:
                    normalized_engines.append(engine_map)
                    continue
                items = coerce_str_list(raw_overlays, field="options_overlays")
                overlay_paths: list[str] = []
                for item in items:
                    candidate = Path(resolve_path_like(item))
                    if not candidate.is_absolute():
                        candidate = (base / candidate).resolve()
                    overlay_paths.append(str(candidate))
                engine_map["options_overlays"] = overlay_paths
                normalized_engines.append(engine_map)
            if normalized_engines:
                payload["engines"] = normalized_engines
        raw_rules_payload = payload.get("rules")
        if raw_rules_payload is None:
            rconf: JsonObject = {}
        elif isinstance(raw_rules_payload, Mapping):
            rconf = coerce_json_object_serialized(raw_rules_payload, field_name="rules")
        else:
            raise TypeError("rules must be a mapping")
        raw_initial_positions = rconf.get("initial_positions")
        if raw_initial_positions is None:
            ipos: JsonObject = {}
        elif isinstance(raw_initial_positions, Mapping):
            ipos = coerce_json_object_serialized(raw_initial_positions, field_name="rules.initial_positions")
        else:
            raise TypeError("rules.initial_positions must be a mapping when provided")
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


# ---- Retired from spsa.py ----
