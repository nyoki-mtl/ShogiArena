"""Helpers for loading live diagnostics guideline configs."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_optional_text,
)
from shogiarena._core.shared.kernel.serialization import json_serialize

from .diagnostics_models import (
    DEFAULT_GUIDELINES,
    WatchlistExtra,
    _guidelines_from_model,
    _GuidelinesConfig,
    _GuidelinesModel,
    deep_copy_guidelines,
)

logger = logging.getLogger(__name__)


def _as_object_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _normalize_watch_extra(entry: Mapping[str, object] | str | None) -> WatchlistExtra:
    default_extra = DEFAULT_GUIDELINES["watchlist"]["extras"][0]
    if isinstance(entry, str):
        key = entry.strip() or default_extra["key"]
        base: WatchlistExtra = {
            "key": default_extra["key"],
            "label": default_extra["label"],
            "unit": default_extra["unit"],
            "warning": default_extra["warning"],
            "critical": default_extra["critical"],
            "should_notify": default_extra["should_notify"],
        }
        if key != default_extra["key"]:
            base.update({"key": key, "label": key, "unit": ""})
        return base
    if not isinstance(entry, Mapping):
        return {
            "key": default_extra["key"],
            "label": default_extra["label"],
            "unit": default_extra["unit"],
            "warning": default_extra["warning"],
            "critical": default_extra["critical"],
            "should_notify": default_extra["should_notify"],
        }
    key_default = default_extra["key"]
    key = coerce_optional_text(entry.get("key")) or key_default
    label_default = default_extra["label"] if key == key_default else key
    label = coerce_optional_text(entry.get("label")) or label_default
    unit_default = default_extra["unit"] if key == key_default else ""
    unit = coerce_optional_text(entry.get("unit")) or unit_default
    warning = coerce_float(entry.get("warning"))
    if warning is None:
        warning = default_extra["warning"]
    critical = coerce_float(entry.get("critical"))
    if critical is None:
        critical = default_extra["critical"]
    if critical < warning:
        critical = warning
    notify_flag = coerce_bool(entry.get("should_notify", default_extra.get("should_notify", False)))
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "warning": warning,
        "critical": critical,
        "should_notify": notify_flag,
    }


def _parse_guidelines(raw: Mapping[str, object]) -> _GuidelinesConfig:
    parsed = deep_copy_guidelines()
    hydrator = _as_object_mapping(raw.get("hydrator"))
    trigger = _as_object_mapping(hydrator.get("trigger_per_hour"))
    failure = _as_object_mapping(hydrator.get("failure_rate"))
    watchlist = _as_object_mapping(raw.get("watchlist"))

    trigger_warning = coerce_float(trigger.get("warning"))
    if trigger_warning is not None:
        parsed["hydrator"]["trigger_per_hour"]["warning"] = trigger_warning
    trigger_critical = coerce_float(trigger.get("critical"))
    if trigger_critical is not None:
        parsed["hydrator"]["trigger_per_hour"]["critical"] = trigger_critical
    failure_warning = coerce_float(failure.get("warning"))
    if failure_warning is not None:
        parsed["hydrator"]["failure_rate"]["warning"] = failure_warning
    failure_critical = coerce_float(failure.get("critical"))
    if failure_critical is not None:
        parsed["hydrator"]["failure_rate"]["critical"] = failure_critical

    extras_input: list[object] = []
    raw_extras = watchlist.get("extras")
    if isinstance(raw_extras, list):
        extras_input.extend(raw_extras)
    if not extras_input:
        extras_serialized = json_serialize(DEFAULT_GUIDELINES["watchlist"]["extras"])
        if isinstance(extras_serialized, list):
            extras_input = list(extras_serialized)

    normalized_extras: list[WatchlistExtra] = []
    for entry in extras_input:
        if entry is None:
            continue
        if isinstance(entry, str) and not entry.strip():
            continue
        if isinstance(entry, Mapping):
            normalized_entry: dict[str, object] = {}
            for key, item in entry.items():
                normalized_entry[str(key)] = json_serialize(item)
            normalized_extras.append(_normalize_watch_extra(normalized_entry))
            continue
        if isinstance(entry, str):
            normalized_extras.append(_normalize_watch_extra(entry))
    parsed_limit = coerce_int(watchlist.get("limit"))
    parsed["watchlist"] = {"limit": max(1, parsed_limit or parsed["watchlist"]["limit"]), "extras": normalized_extras}

    auto_snapshot_raw = raw.get("auto_snapshot")
    if isinstance(auto_snapshot_raw, Mapping):
        auto_snapshot = _as_object_mapping(auto_snapshot_raw)
        interval = max(0, coerce_int(auto_snapshot.get("interval_seconds")) or 0)
        mode = (coerce_optional_text(auto_snapshot.get("mode")) or "console").lower()
        destination = (coerce_optional_text(auto_snapshot.get("destination")) or "").lower()
        if destination not in {"console", "clipboard", "api"}:
            destination = "api" if mode == "clipboard" else "console"
        retention = max(0, coerce_int(auto_snapshot.get("retention_minutes")) or 0)
        parsed["auto_snapshot"] = {
            "interval_seconds": interval,
            "mode": "clipboard" if mode == "clipboard" else "console",
            "destination": destination,
            "retention_minutes": retention,
        }

    try:
        validated = _GuidelinesModel.model_validate(parsed)
    except ValidationError as exc:
        logger.error("Failed to validate live diagnostics config. Falling back to defaults: %s", exc)
        return deep_copy_guidelines()
    return _guidelines_from_model(validated)


def _safe_load_yaml(path: Path) -> Mapping[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except FileNotFoundError:
        return None
    except yaml.YAMLError as exc:  # pragma: no cover - configuration errors logged for operators
        logger.warning("Failed to parse live diagnostics config %s: %s", path, exc)
        return None
    if isinstance(data, Mapping):
        return to_json_object(data)
    logger.warning("Live diagnostics config %s must be a mapping", path)
    return None


def _candidate_paths(preferred: Path | None) -> list[Path]:
    """探索する設定ファイルの候補を、優先度順に返す。

    リポジトリ固有の開発用パスは候補に含めない。run ディレクトリ外の設定を
    使いたい場合は ``SHOGI_ARENA_LIVE_DIAGNOSTICS_CONFIG`` で明示する。
    """

    candidates: list[Path] = []
    if preferred:
        candidates.append(preferred)
    env_override = os.getenv("SHOGI_ARENA_LIVE_DIAGNOSTICS_CONFIG")
    if env_override:
        candidates.append(Path(env_override))
    return candidates


def load_live_diagnostics_guidelines(preferred_path: Path | None = None) -> _GuidelinesConfig:
    """Load live diagnostics guidelines from YAML, falling back to defaults."""

    for candidate in _candidate_paths(preferred_path):
        resolved = candidate.expanduser()
        if not resolved.exists():
            continue
        data = _safe_load_yaml(resolved)
        if data is None:
            continue
        return _parse_guidelines(data)
    return deep_copy_guidelines()
