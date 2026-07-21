"""Tournament run configuration model and mapping loader."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.engine_book import collect_book_preflight_errors, is_engine_book_enabled
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import PATH_OPTION_KEYS, resolve_path_like
from shogiarena._core.shared.kernel.run_artifact_contract import (
    build_run_artifact_payload_bundle,
    build_schedule_payload,
)
from shogiarena._core.shared.kernel.run_artifact_hashes import RunArtifactHashBundle
from shogiarena._core.shared.kernel.run_artifact_hashes import schedule_hash as compute_schedule_hash
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
    LoggingConfig,
    RatingConfig,
    RecordOutputConfig,
    SystemConfig,
    TournamentConfig,
)

logger = logging.getLogger(__name__)


class TournamentRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_name: str
    engines: list[EngineConfig]
    tournament: TournamentConfig = Field(default_factory=TournamentConfig)
    generate: GenerateConfig | None = None
    rules: RulesConfig = Field(default_factory=RulesConfig)
    sprt: SprtConfig | None = None
    openbench: OpenBenchConfig | None = None
    rating: RatingConfig = Field(default_factory=RatingConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    log_level: str = "INFO"
    system: SystemConfig = Field(default_factory=SystemConfig)
    records_output: RecordOutputConfig | None = None

    instances: tuple[Path, ...] | None = None
    output_dir: Path = Field(default_factory=lambda: project_dirs.output_dir)
    source_path: Path | None = None

    _run_artifact_hashes_cache: RunArtifactHashBundle | None = PrivateAttr(default=None)
    _schedule_hash_cache: str | None = PrivateAttr(default=None)

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
                "path_preflight",
                "resource_capacity_preflight",
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
            tested_engine = self.sprt.tested_engine
            if tested_engine is None:
                # Keep the CLI/list-order compatibility contract, but materialize the role in the
                # resolved config so artifacts and diagnostics never leave it implicit.
                self.sprt.tested_engine = engine_names[0]
            else:
                if tested_engine not in engine_names:
                    raise ValueError(f"sprt.tested_engine must name one of engines: {tested_engine!r}")
                tested_index = engine_names.index(tested_engine)
                if tested_index != 0:
                    self.engines.insert(0, self.engines.pop(tested_index))
                    engine_names.insert(0, engine_names.pop(tested_index))

            if self.sprt.max_games is not None:
                max_games = int(self.sprt.max_games)
                if (
                    "games_per_pair" in self.tournament.model_fields_set
                    and int(self.tournament.games_per_pair) != max_games
                ):
                    raise ValueError("sprt.max_games and tournament.games_per_pair must match when both are specified")
                self.tournament.games_per_pair = max_games
            if self.sprt.num_parallel is not None:
                sprt_num_parallel = int(self.sprt.num_parallel)
                if (
                    "num_parallel" in self.tournament.model_fields_set
                    and int(self.tournament.num_parallel) != sprt_num_parallel
                ):
                    raise ValueError("sprt.num_parallel and tournament.num_parallel must match when both are specified")
                self.tournament.num_parallel = sprt_num_parallel

        self._preflight_path_options()

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

    def _preflight_path_options(self) -> None:
        mode = self.system.path_preflight
        if mode == "off":
            return
        errors: list[str] = []
        for engine in self.engines:
            merged = self._merged_engine_options(engine)
            working_dir = self._engine_working_dir(engine)
            # 内蔵定跡が有効なら BookDir は composite 検証に委ね、scalar 検証から除外する。
            # （絶対 BookFile は BookDir を無視する YaneuraOu 挙動とも整合し、二重/誤検知を避ける）。
            book_enabled = is_engine_book_enabled(merged)
            for option_name, resolved_path in self._iter_path_option_values(engine, merged, skip_book_dir=book_enabled):
                candidate = Path(resolved_path)
                # 相対 path は実行時 cwd（working_dir）基準で存在確認し、ランタイムと揃える。
                if not candidate.is_absolute() and working_dir is not None:
                    candidate = working_dir / candidate
                if candidate.exists():
                    continue
                message = f"Engine '{engine.name}' path option '{option_name}' references missing path: {candidate}"
                if mode == "error":
                    errors.append(message)
                else:
                    logger.warning("%s", message)
            # 内蔵定跡(A) の composite 検証（BookDir+BookFile）。USI_OwnBook が false で
            # 明示無効化されておらず BookFile != no_book のときのみ発動する（Task 0014）。
            book_errors = collect_book_preflight_errors(
                merged,
                engine_name=engine.name,
                output_dir=self.output_dir,
                engine_dir=project_dirs.engine_dir,
                working_dir=working_dir,
            )
            for message in book_errors:
                if mode == "error":
                    errors.append(message)
                else:
                    logger.warning("%s", message)
        if errors:
            raise FileNotFoundError("; ".join(errors))

    def _merged_engine_options(self, engine: EngineConfig) -> JsonObject:
        merged = self._load_engine_file_options(engine)
        merged.update(engine.load_overlay_options())
        merged.update({str(key): json_serialize(value) for key, value in engine.options.items()})
        return merged

    def _iter_path_option_values(
        self, engine: EngineConfig, merged: JsonObject, *, skip_book_dir: bool = False
    ) -> list[tuple[str, str]]:
        option_names = set(PATH_OPTION_KEYS)
        option_names.update(engine.path_options)
        if skip_book_dir:
            option_names.discard("BookDir")
        if not option_names:
            return []
        values: list[tuple[str, str]] = []
        for option_name in sorted(option_names):
            raw_value = merged.get(option_name)
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            resolved = resolve_path_like(raw_value, output_dir=self.output_dir, engine_dir=project_dirs.engine_dir)
            values.append((option_name, resolved))
        return values

    def _load_engine_file_options(self, engine: EngineConfig) -> JsonObject:
        if engine.engine_path is None:
            return {}
        config = UsiEngineConfig.from_file(
            engine.engine_path,
            output_dir=self.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
        return {str(key): json_serialize(value) for key, value in config.options.items()}

    def _engine_working_dir(self, engine: EngineConfig) -> Path | None:
        """エンジンプロセスの実行時 cwd を preflight 用に推定する。

        ランタイムは ``working_directory or <engine binary parent>`` を cwd にする
        (`runtime_factory`)。これを再現して相対 ``BookDir`` の存在確認基準を実行時と揃える。
        artifact 指定で engine_path 不明な場合は ``None``（cwd 基準にフォールバック）。
        """

        if engine.engine_path is None:
            return None
        config = UsiEngineConfig.from_file(
            engine.engine_path,
            output_dir=self.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
        if config.working_directory:
            return Path(config.working_directory)
        if config.engine_path:
            return Path(config.engine_path).parent
        return None

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
            raise ValueError(f"Unknown keys in TournamentRunConfig data: {', '.join(extras)}")
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
                engine_path = engine_map.get("engine_path")
                if isinstance(engine_path, str) and engine_path.strip():
                    candidate = Path(resolve_path_like(engine_path))
                    if not candidate.is_absolute():
                        candidate = (base / candidate).resolve()
                    engine_map["engine_path"] = str(candidate)
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
        cached = self._schedule_hash_cache
        if cached is not None:
            return cached
        payload = self.model_dump(mode="json")
        digest = compute_schedule_hash(build_schedule_payload(payload))
        self._schedule_hash_cache = digest
        return digest

    def get_resume_hash(self) -> str:
        resume_digest = self.get_run_artifact_hashes().resume_hash
        if resume_digest is None:
            raise ValueError("resume_hash requires sealed provenance")
        return resume_digest

    def get_run_artifact_hashes(self) -> RunArtifactHashBundle:
        cached = self._run_artifact_hashes_cache
        if cached is not None:
            return cached
        payload = self.model_dump(mode="json")
        hashes = build_run_artifact_payload_bundle(payload).hashes
        self._run_artifact_hashes_cache = hashes
        self._schedule_hash_cache = hashes.schedule_hash
        return hashes

    def clear_run_artifact_hash_cache(self) -> None:
        """Clear memoized run artifact hashes after config mutation."""

        self._run_artifact_hashes_cache = None
        self._schedule_hash_cache = None

    def has_unresolved_artifacts(self) -> bool:
        """Return true when provenance needs artifact resolution before hashing."""

        for engine in self.engines:
            if engine.artifact and engine.engine_path is None:
                return True
        return False


# ---- Retired from spsa.py ----
