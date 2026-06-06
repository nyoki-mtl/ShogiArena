"""Shared builders for run artifact hash payloads."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import (
    RunArtifactHashBundle,
    RunArtifactHashRequest,
    build_run_artifact_hash_bundle,
)
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)

_TOURNAMENT_RUNTIME_KEYS = {"num_parallel"}
_SPRT_SCHEDULE_EXCLUDED_KEYS = {"elo0", "elo1", "alpha", "beta", "min_games", "max_games", "num_parallel"}


@dataclass(frozen=True, slots=True)
class RunArtifactPayloadBundle:
    """Canonical payloads and hashes for one run config."""

    config_payload: JsonObject
    schedule_payload: JsonObject
    provenance_payload: JsonObject
    sprt_payload: JsonObject | None
    hashes: RunArtifactHashBundle


def build_run_artifact_payload_bundle(config_payload: Mapping[str, object]) -> RunArtifactPayloadBundle:
    """Build canonical run artifact payloads and hashes from a config payload."""

    canonical_config = _object_or_empty(config_payload)
    schedule_payload = build_schedule_payload(canonical_config)
    provenance_payload = build_provenance_payload(canonical_config)
    sprt_payload = _object_or_empty(canonical_config.get("sprt")) or None
    hashes = build_run_artifact_hash_bundle(
        RunArtifactHashRequest(
            config_payload=canonical_config,
            schedule_payload=schedule_payload,
            provenance_payload=provenance_payload,
            sprt_payload=sprt_payload,
        )
    )
    return RunArtifactPayloadBundle(
        config_payload=canonical_config,
        schedule_payload=schedule_payload,
        provenance_payload=provenance_payload,
        sprt_payload=sprt_payload,
        hashes=hashes,
    )


def build_schedule_payload(config_payload: Mapping[str, object]) -> JsonObject:
    """Build the logical run plan used for grouping."""

    config = _object_or_empty(config_payload)
    generate = _object_or_empty(config.get("generate"))
    spsa = _spsa_contract_payload(config)
    sprt = _object_or_empty(config.get("sprt"))
    tournament = _schedule_tournament_payload(_object_or_empty(config.get("tournament")), is_sprt=bool(sprt))
    kind = _schedule_kind(config, generate=generate, spsa=spsa)
    payload: JsonObject = {
        "kind": kind,
        "experiment_name": config.get("experiment_name"),
        "rules": _object_or_empty(config.get("rules")),
        "engines": [_logical_engine_payload(engine) for engine in _config_engines(config)],
    }
    if tournament:
        payload["tournament"] = tournament
    if generate:
        payload["generate"] = _generate_schedule_payload(generate)
    if spsa:
        payload["spsa"] = _spsa_schedule_payload(spsa)
    if sprt:
        logical_sprt = {key: value for key, value in sprt.items() if key not in _SPRT_SCHEDULE_EXCLUDED_KEYS}
        if logical_sprt:
            payload["sprt"] = logical_sprt
    return payload


def build_provenance_payload(config_payload: Mapping[str, object]) -> JsonObject:
    """Build physical provenance payload used for resume safety."""

    config = _object_or_empty(config_payload)
    engines = [_engine_provenance_payload(engine) for engine in _config_engines(config)]
    hash_source = "sha256"
    for engine in engines:
        if engine.get("hash_source") != "sha256":
            hash_source = "missing"
            break
    return {
        "hash_source": hash_source,
        "engines": engines,
    }


def build_engine_manifest_payload(engine: Mapping[str, object]) -> JsonObject:
    """Build one manifest engine entry."""

    engine_map = _object_or_empty(engine)
    physical = _physical_engine_payload(engine_map)
    engine_path_text = coerce_str(physical.get("engine_path"))
    engine_config_text = coerce_str(physical.get("engine_config"))
    working_dir_text = coerce_str(physical.get("working_directory")) or coerce_str(physical.get("working_dir"))
    if working_dir_text is None and engine_path_text is not None:
        working_dir_text = str(Path(engine_path_text).parent)
    sha256 = _sha256_file(Path(engine_path_text)) if engine_path_text is not None else None
    return {
        "name": coerce_str(engine_map.get("name")) or coerce_str(physical.get("name")),
        "artifact": coerce_str(engine_map.get("artifact")) or coerce_str(physical.get("artifact")),
        "resolved_paths": {
            "engine_path": engine_path_text,
            "engine_config": engine_config_text,
            "working_directory": working_dir_text,
            "path_options": _resolved_path_options_payload(engine_map),
        },
        "bytes_hash": {
            "engine_binary_sha256": sha256,
            "path_options": _path_option_hashes_payload(engine_map),
        },
        "effective_options": _effective_options_payload(engine_map),
        "path_options": _path_option_names(engine_map),
        "build_options": _object_or_empty(engine_map.get("build_options"))
        or _object_or_empty(physical.get("build_options")),
        "verification_probe": None,
    }


def build_engine_manifest_payloads(config_payload: Mapping[str, object]) -> list[JsonObject]:
    """Build manifest engine entries for tournament or SPSA-shaped config."""

    config = _object_or_empty(config_payload)
    return [build_engine_manifest_payload(engine) for engine in _config_engines(config)]


def _schedule_kind(config: JsonObject, *, generate: JsonObject, spsa: JsonObject) -> str:
    if spsa or "baseline" in config or "tuned" in config:
        return "spsa"
    if generate:
        return "generate"
    return "tournament"


def _schedule_tournament_payload(tournament: JsonObject, *, is_sprt: bool) -> JsonObject:
    payload = {key: value for key, value in tournament.items() if key not in _TOURNAMENT_RUNTIME_KEYS}
    if is_sprt:
        payload.pop("games_per_pair", None)
    return payload


def _generate_schedule_payload(generate: JsonObject) -> JsonObject:
    return dict(generate)


def _spsa_schedule_payload(spsa: JsonObject) -> JsonObject:
    keys = ("space", "space_path", "num_updates", "pairs_per_update", "algorithm", "variants", "seed")
    return {key: spsa[key] for key in keys if key in spsa}


def _spsa_contract_payload(config: JsonObject) -> JsonObject:
    nested = _object_or_empty(config.get("spsa"))
    if nested:
        return nested
    if "baseline" not in config and "tuned" not in config and "num_updates" not in config:
        return {}
    keys = (
        "space_path",
        "start_sfens_path",
        "num_updates",
        "pairs_per_update",
        "crn_enabled",
        "is_crn_enabled",
        "int_rounding",
        "update_mode",
        "a0",
        "A",
        "alpha",
        "gamma",
        "mobility",
        "scale",
    )
    return {key: config[key] for key in keys if key in config}


def _config_engines(config: JsonObject) -> list[JsonObject]:
    engines = _list_of_objects(config.get("engines"))
    if engines:
        return engines
    combined: list[JsonObject] = []
    combined.extend(_list_of_objects(config.get("baseline")))
    combined.extend(_list_of_objects(config.get("tuned")))
    return combined


def _logical_engine_payload(engine: JsonObject) -> JsonObject:
    payload: JsonObject = {
        "name": coerce_str(engine.get("name")),
        "artifact": coerce_str(engine.get("artifact")),
        "effective_options": _effective_options_payload(engine),
        "path_options": _path_option_names(engine),
    }
    time_control = engine.get("time_control")
    if time_control is not None:
        payload["time_control"] = json_serialize(time_control)
    go_options = _object_or_empty(engine.get("go_options"))
    if go_options:
        payload["go_options"] = go_options
    return payload


def _engine_provenance_payload(engine: JsonObject) -> JsonObject:
    manifest_payload = build_engine_manifest_payload(engine)
    bytes_hash = _object_or_empty(manifest_payload.get("bytes_hash"))
    return {
        "name": manifest_payload.get("name"),
        "artifact": manifest_payload.get("artifact"),
        "resolved_paths": manifest_payload.get("resolved_paths"),
        "bytes_hash": manifest_payload.get("bytes_hash"),
        "build_options": _object_or_empty(engine.get("build_options"))
        or _object_or_empty(manifest_payload.get("build_options")),
        "hash_source": "sha256" if bytes_hash.get("engine_binary_sha256") else "missing",
    }


def _physical_engine_payload(engine: JsonObject) -> JsonObject:
    engine_path_text = coerce_str(engine.get("engine_path"))
    if engine_path_text is None:
        return dict(engine)
    engine_path = Path(engine_path_text)
    if engine_path.suffix.lower() not in {".yaml", ".yml"} or not engine_path.is_file():
        return dict(engine)
    try:
        raw = yaml.safe_load(engine_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("Failed to read engine config for provenance %s: %s", engine_path, exc)
        return dict(engine)
    if not isinstance(raw, Mapping):
        return dict(engine)
    physical = _object_or_empty(raw)
    physical.setdefault("engine_config", engine_path_text)
    for key in ("name", "artifact", "build_options", "working_directory", "working_dir"):
        if key in engine and key not in physical:
            physical[key] = engine[key]
    return physical


def _effective_options_payload(engine: JsonObject) -> JsonObject:
    path_options = set(_path_option_names(engine))
    options = _object_or_empty(engine.get("options"))
    return {key: value for key, value in options.items() if key not in path_options}


def _resolved_path_options_payload(engine: JsonObject) -> JsonObject:
    path_options = set(_path_option_names(engine))
    options = _object_or_empty(engine.get("options"))
    return {key: value for key, value in options.items() if key in path_options}


def _path_option_hashes_payload(engine: JsonObject) -> JsonObject:
    resolved = _resolved_path_options_payload(engine)
    result: JsonObject = {}
    for key, raw_path in resolved.items():
        path_text = coerce_str(raw_path)
        result[key] = _sha256_path(Path(path_text)) if path_text else None
    return result


def _path_option_names(engine: JsonObject) -> list[str]:
    raw = engine.get("path_options")
    if not isinstance(raw, list | tuple):
        return []
    names: list[str] = []
    for item in raw:
        name = coerce_str(item)
        if name and name not in names:
            names.append(name)
    return names


def _sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        logger.debug("Failed to hash engine binary %s: %s", path, exc)
        return None


def _sha256_path(path: Path) -> str | None:
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        return None
    try:
        digest = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            rel = child.relative_to(path).as_posix()
            child_hash = _sha256_file(child)
            stat = child.stat()
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update((child_hash or "").encode("ascii"))
        return digest.hexdigest()
    except OSError as exc:
        logger.debug("Failed to hash path option directory %s: %s", path, exc)
        return None


def _object_or_empty(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): json_serialize(item) for key, item in value.items()}


def _list_of_objects(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [_object_or_empty(item) for item in value]


def total_scheduled_games(config_payload: Mapping[str, object]) -> int | None:
    """Best-effort total scheduled game count for manifest summaries."""

    config = _object_or_empty(config_payload)
    tournament = _object_or_empty(config.get("tournament"))
    generate = _object_or_empty(config.get("generate"))
    engines = _config_engines(config)
    generate_games = coerce_int(generate.get("games"))
    if generate_games is not None:
        return generate_games
    games_per_pair = coerce_int(tournament.get("games_per_pair"))
    if games_per_pair is None or len(engines) < 2:
        return None
    return (len(engines) * (len(engines) - 1) // 2) * games_per_pair


__all__ = [
    "RunArtifactPayloadBundle",
    "build_engine_manifest_payload",
    "build_engine_manifest_payloads",
    "build_provenance_payload",
    "build_run_artifact_payload_bundle",
    "build_schedule_payload",
    "total_scheduled_games",
]
