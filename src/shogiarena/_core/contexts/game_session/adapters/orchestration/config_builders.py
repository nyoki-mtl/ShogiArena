"""Config and payload builder helpers used by runtime orchestrators.

These helpers were extracted from ``arena_orchestrator.py`` to keep the file
focused on orchestration control flow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import to_json_value
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.overlay_options import select_overlay_options
from shogiarena._core.shared.kernel.paths import resolve_path_like

from .config_engine import EngineConfig


def _load_overlay_payload(path: Path) -> JsonObject:
    if not path.exists():
        raise FileNotFoundError(f"overlay config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"overlay YAML must be a mapping: {path}")
    payload: JsonObject = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise TypeError(f"overlay YAML keys must be strings: {path}")
        json_value = to_json_value(value)
        if json_value is None and value is not None:
            raise TypeError(f"overlay YAML contains non-serializable value for key {key}: {path}")
        payload[key] = json_value
    return payload


def _extract_overlay_options(payload: Mapping[str, JsonValue], *, path: Path) -> JsonObject:
    overlay_opts = select_overlay_options(payload, source=str(path))
    result: JsonObject = {}
    for key, value in overlay_opts.items():
        json_value = to_json_value(value)
        if json_value is None and value is not None:
            raise TypeError(f"overlay option contains non-serializable value for key {key}: {path}")
        result[key] = json_value
    return result


_ENGINE_OVERLAY_KEYS: tuple[str, ...] = (
    "mate_default_ply_limit",
    "mate_default_node_limit",
    "mate_default_infinite",
    "mate_wait_for_bestmove",
    "isready_sync_strategy",
    "isready_lock_key",
    "isready_lock_template",
    "isready_lock_check_key",
    "isready_lock_check_template",
    "isready_lock_check_templates",
    "isready_lock_skip_if_exists",
)

_BOOL_ENGINE_OVERLAY_KEYS = frozenset(
    {"mate_default_infinite", "mate_wait_for_bestmove", "isready_lock_skip_if_exists"}
)
_INT_ENGINE_OVERLAY_KEYS = frozenset({"mate_default_ply_limit", "mate_default_node_limit"})


@dataclass(frozen=True, slots=True)
class UsiOptionLayers:
    """固定precedenceを保ったarena側USI option layer。"""

    artifact_overlay: JsonObject
    arena: JsonObject
    declared_overlays: JsonObject
    inline: JsonObject

    def merged(self) -> JsonObject:
        """artifact < arena < declared overlay < inlineでmergeする。"""

        merged: JsonObject = {}
        for layer in (
            self.artifact_overlay,
            self.arena,
            self.declared_overlays,
            self.inline,
        ):
            merged.update(layer)
        return merged


def _engine_overlay_target_field(key: str) -> str | None:
    for field_name, field_info in EngineConfig.model_fields.items():
        if key == field_name or key == field_info.alias:
            return field_name
    return None


def _apply_engine_overlay(engine_spec: EngineConfig, payload: Mapping[str, JsonValue]) -> None:
    engine_payload = payload.get("engine")
    if not isinstance(engine_payload, Mapping):
        return
    engine_values = {str(key): value for key, value in engine_payload.items()}
    for key in _ENGINE_OVERLAY_KEYS:
        target_attr = _engine_overlay_target_field(key)
        if target_attr is None or target_attr in engine_spec.model_fields_set:
            continue
        value = engine_values.get(key)
        if isinstance(value, str) and value.strip():
            setattr(engine_spec, target_attr, value.strip())
        elif key in _BOOL_ENGINE_OVERLAY_KEYS and isinstance(value, bool):
            setattr(engine_spec, target_attr, value)
        elif key in _INT_ENGINE_OVERLAY_KEYS and isinstance(value, int) and value > 0:
            setattr(engine_spec, target_attr, value)
        elif key == "isready_lock_check_templates" and isinstance(value, list):
            cleaned = [item for item in value if isinstance(item, str) and item.strip()]
            if cleaned:
                setattr(engine_spec, target_attr, tuple(cleaned))


def build_usi_options(base_extra: JsonObject | None, engine_spec: EngineConfig) -> JsonObject | None:
    """Merge arena-level extra options with engine-specific overlays/options."""
    layers = build_usi_option_layers(base_extra, engine_spec)
    merged = layers.merged()
    return merged or None


def build_usi_option_layers(base_extra: JsonObject | None, engine_spec: EngineConfig) -> UsiOptionLayers:
    """Resolverへ渡すoption layerをprecedenceを潰さず構築する。"""

    artifact_overlay: JsonObject = {}
    artifact = engine_spec.artifact
    if isinstance(artifact, str) and artifact.strip():
        repo_name = artifact.split("/", 1)[0]
        overlay_path = project_dirs.overlays.get(repo_name)
        if overlay_path is not None:
            payload = _load_overlay_payload(overlay_path)
            _apply_engine_overlay(engine_spec, payload)
            artifact_overlay.update(_extract_overlay_options(payload, path=overlay_path))

    arena = dict(base_extra or {})
    options_overlays = engine_spec.options_overlays
    for overlay_path in options_overlays:
        if not isinstance(overlay_path, Path):
            overlay_path = Path(str(overlay_path))
        payload = _load_overlay_payload(overlay_path)
        _apply_engine_overlay(engine_spec, payload)
    overlay_opts = engine_spec.load_overlay_options()
    inline_opts = {str(k): v for k, v in engine_spec.options.items()}

    for layer in (artifact_overlay, arena, overlay_opts, inline_opts):
        _resolve_option_paths(layer)
    return UsiOptionLayers(
        artifact_overlay=artifact_overlay,
        arena=arena,
        declared_overlays=overlay_opts,
        inline=inline_opts,
    )


def _resolve_option_paths(options: JsonObject) -> None:
    for key, value in list(options.items()):
        if not isinstance(value, str):
            continue
        resolved = resolve_path_like(
            value,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
        options[key] = resolved


def compute_max_ply_extra_options(rules: Any) -> JsonObject | None:
    """Build extra engine options to keep engine max-move limits in sync."""
    adj_settings = rules.adjudication
    if not adj_settings.is_max_plies_enabled:
        return None
    if not adj_settings.should_sync_max_plies_with_engine:
        return None

    moves_value = int(adj_settings.max_plies if adj_settings.max_plies is not None else 0)
    names_cfg = adj_settings.engine_max_ply_option_names
    option_names: list[str] = []
    if isinstance(names_cfg, str):
        if names_cfg.lower() == "auto":
            option_names = ["MaxMovesToDraw", "Draw_Ply"]
        else:
            option_names = [part.strip() for part in names_cfg.replace("|", ",").split(",") if part.strip()]
    else:
        for entry in names_cfg:
            option_names.extend(part.strip() for part in str(entry).replace("|", ",").split(",") if part.strip())

    seen: set[str] = set()
    deduped: list[str] = []
    for name in option_names:
        if name and name not in seen:
            seen.add(name)
            deduped.append(name)
    if not deduped:
        return None
    key = "|".join(deduped)
    return {key: moves_value}


def build_engine_config_map(engines: list[Any]) -> dict[str, Any]:  # I/O boundary: engine specs are heterogeneous
    """Build a name-to-engine-spec map from a list of engine specs."""
    mapped: dict[str, Any] = {}  # I/O boundary: values remain opaque until orchestrator wiring
    for engine in engines:
        name = getattr(engine, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("Engine config must have a non-empty name")
        mapped[name] = engine
    return mapped


def create_progress_queue() -> asyncio.Queue[tuple[int, int, str | None]]:
    """Factory for the progress queue shared by orchestrators."""
    return asyncio.Queue()


__all__ = [
    "build_engine_config_map",
    "build_usi_option_layers",
    "build_usi_options",
    "compute_max_ply_extra_options",
    "create_progress_queue",
    "UsiOptionLayers",
]
