"""Artifact-driven engine config materialization for orchestrators."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import build_usi_options
from shogiarena._core.shared.kernel.hash_normalization import normalize_for_hash
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort

from .config_engine import EngineConfig

_ARTIFACT_REF_RE = re.compile(r"^([A-Za-z0-9._-]+)/([A-Fa-f0-9]{6,40})$")


def _build_artifact_config_filename(
    artifact: str,
    build_options: Mapping[str, JsonValue],
    payload: Mapping[str, object] | None = None,
) -> str:
    normalized_payload = {
        "artifact": artifact.strip(),
        "build_options": normalize_for_hash(dict(build_options)),
        "payload": normalize_for_hash(dict(payload or {})),
    }
    raw = json.dumps(normalized_payload, sort_keys=True, separators=(",", ":"), default=str)
    hash_suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    match = _ARTIFACT_REF_RE.match(artifact.strip())
    repo = match.group(1).lower() if match else "artifact"
    commit = match.group(2).lower() if match else None
    return f"{repo}_{commit}_{hash_suffix}.yaml" if commit else f"{repo}_{hash_suffix}.yaml"


def _build_artifact_config_payload(
    engine: EngineConfig,
    *,
    artifact: str,
    build_options: Mapping[str, JsonValue],
    resolved_engine_path: Path | None = None,
) -> JsonObject:
    payload: JsonObject = {"artifact": artifact, "build_options": dict(build_options)}
    if resolved_engine_path is not None:
        payload["engine_path"] = str(resolved_engine_path)
    if engine.name:
        payload["name"] = engine.name
    if engine.options:
        payload["options"] = dict(engine.options)
    if engine.go_options:
        payload["go_options"] = dict(engine.go_options)
    if engine.path_options:
        payload["path_options"] = list(engine.path_options)
    if engine.options_overlays:
        payload["options_overlays"] = [str(path) for path in engine.options_overlays]
    if engine.mate_default_ply_limit is not None and engine.mate_default_ply_limit > 0:
        payload["mate_default_ply_limit"] = engine.mate_default_ply_limit
    if engine.mate_default_node_limit is not None and engine.mate_default_node_limit > 0:
        payload["mate_default_node_limit"] = engine.mate_default_node_limit
    payload["mate_default_infinite"] = engine.is_mate_default_infinite
    payload["mate_wait_for_bestmove"] = engine.should_mate_wait_for_bestmove
    if engine.isready_sync_strategy:
        payload["isready_sync_strategy"] = engine.isready_sync_strategy
    if engine.isready_lock_key and engine.isready_lock_key.strip():
        payload["isready_lock_key"] = engine.isready_lock_key.strip()
    if engine.isready_lock_template and engine.isready_lock_template.strip():
        payload["isready_lock_template"] = engine.isready_lock_template.strip()
    if engine.isready_lock_check_key and engine.isready_lock_check_key.strip():
        payload["isready_lock_check_key"] = engine.isready_lock_check_key.strip()
    if engine.isready_lock_check_template and engine.isready_lock_check_template.strip():
        payload["isready_lock_check_template"] = engine.isready_lock_check_template.strip()
    if engine.isready_lock_check_templates:
        payload["isready_lock_check_templates"] = [str(item) for item in engine.isready_lock_check_templates]
    payload["isready_lock_skip_if_exists"] = engine.should_skip_isready_lock_if_exists
    if engine.handshake_timeout is not None:
        payload["handshake_timeout"] = float(engine.handshake_timeout)
    return payload


def _materialize_engine_config_from_artifact(
    engine: EngineConfig,
    *,
    output_dir: Path,
    artifact_resolver: ArtifactResolutionPort | None = None,
) -> Path | None:
    """Write artifact-based engine config YAML and return its path.

    Returns ``None`` when the engine does not define an artifact reference.
    """

    artifact = engine.artifact
    if not artifact or not artifact.strip():
        return None

    build_options = engine.build_options or {}
    resolved_engine_path = Path(artifact_resolver(artifact, build_options)) if artifact_resolver is not None else None
    payload = _build_artifact_config_payload(
        engine,
        artifact=artifact,
        build_options=build_options,
        resolved_engine_path=resolved_engine_path,
    )
    filename = _build_artifact_config_filename(artifact, build_options, payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / filename
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return out_path


def resolve_engine_config_entry(
    engine: EngineConfig,
    *,
    output_dir: Path,
    extra_options: JsonObject | None,
    artifact_resolver: ArtifactResolutionPort | None = None,
) -> EngineConfig:
    """Resolve a concrete engine config path from existing file or artifact."""

    # Apply overlay-provided settings before any materialization.
    build_usi_options(extra_options, engine)

    if engine.engine_path is not None and engine.engine_path.exists():
        return engine

    materialized = _materialize_engine_config_from_artifact(
        engine,
        output_dir=output_dir,
        artifact_resolver=artifact_resolver,
    )
    if materialized is not None:
        engine.engine_path = materialized
        return engine

    raise ValueError(f"Engine '{engine.name or 'engine'}' must provide engine_path or artifact with build_options")


__all__ = ["resolve_engine_config_entry"]
