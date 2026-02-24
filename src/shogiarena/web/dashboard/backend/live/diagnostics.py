"""Helpers for loading live diagnostics guideline configs."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

import yaml

from shogiarena.utils.types.types import JsonValue

logger = logging.getLogger(__name__)


class _ThresholdConfig(TypedDict):
    warning: float
    critical: float


class _HydratorConfig(TypedDict):
    triggerPerHour: _ThresholdConfig
    failureRate: _ThresholdConfig


class WatchlistExtra(TypedDict):
    key: str
    label: str
    unit: str
    warning: float
    critical: float
    notify: bool


class _WatchlistConfig(TypedDict):
    limit: int
    extras: list[WatchlistExtra]


class _AutoSnapshotConfig(TypedDict):
    intervalSeconds: int
    mode: str
    destination: str
    retentionMinutes: int


class _GuidelinesConfig(TypedDict):
    hydrator: _HydratorConfig
    watchlist: _WatchlistConfig
    autoSnapshot: _AutoSnapshotConfig


DEFAULT_GUIDELINES: _GuidelinesConfig = {
    "hydrator": {
        "triggerPerHour": {"warning": 45, "critical": 60},
        "failureRate": {"warning": 0.03, "critical": 0.05},
    },
    "watchlist": {
        "limit": 6,
        "extras": [
            {
                "key": "payloadKb",
                "label": "Payload (KB)",
                "unit": "KB",
                "warning": 200,
                "critical": 320,
                "notify": False,
            },
            {
                "key": "latencyMs",
                "label": "Latency (ms)",
                "unit": "ms",
                "warning": 500,
                "critical": 1500,
                "notify": False,
            },
            {
                "key": "retries",
                "label": "Retries",
                "unit": "",
                "warning": 1,
                "critical": 3,
                "notify": False,
            },
            {
                "key": "detailPayloadKbSlim",
                "label": "Detail slim payload (KB)",
                "unit": "KB",
                "warning": 180,
                "critical": 240,
                "notify": False,
            },
            {
                "key": "detailPayloadKbFull",
                "label": "Detail full payload (KB)",
                "unit": "KB",
                "warning": 220,
                "critical": 300,
                "notify": False,
            },
            {
                "key": "detailIncludeCount",
                "label": "Detail include count",
                "unit": "fields",
                "warning": 4,
                "critical": 6,
                "notify": False,
            },
        ],
    },
    "autoSnapshot": {
        "intervalSeconds": 0,
        "mode": "console",
        "destination": "console",
        "retentionMinutes": 0,
    },
}


def _deep_copy_guidelines(data: Mapping[str, JsonValue] | None = None) -> _GuidelinesConfig:
    base = DEFAULT_GUIDELINES if data is None else data
    return cast(_GuidelinesConfig, json.loads(json.dumps(base)))


def _coerce_float(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    if not isinstance(value, int | float | str):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_watch_extra(entry: Mapping[str, JsonValue] | str | None) -> WatchlistExtra:
    default_extra = DEFAULT_GUIDELINES["watchlist"]["extras"][0]
    if isinstance(entry, str):
        key = entry.strip() or default_extra["key"]
        base = dict(default_extra)
        if key != default_extra["key"]:
            base.update({"key": key, "label": key, "unit": ""})
        return cast(WatchlistExtra, base)
    if not isinstance(entry, Mapping):
        return cast(WatchlistExtra, json.loads(json.dumps(default_extra)))
    key = str(entry.get("key", default_extra["key"]) or default_extra["key"])
    label = str(entry.get("label", default_extra["label"] if key == default_extra["key"] else key))
    unit = str(entry.get("unit", default_extra["unit"] if key == default_extra["key"] else ""))
    warning = _coerce_float(entry.get("warning"), default_extra["warning"])
    critical = _coerce_float(entry.get("critical"), default_extra["critical"])
    if critical < warning:
        critical = warning
    notify_flag = bool(entry.get("notify", default_extra.get("notify", False)))
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "warning": warning,
        "critical": critical,
        "notify": notify_flag,
    }


def _parse_guidelines(raw: Mapping[str, JsonValue]) -> _GuidelinesConfig:
    parsed = _deep_copy_guidelines()
    hydrator_raw = raw.get("hydrator")
    hydrator = hydrator_raw if isinstance(hydrator_raw, dict) else {}
    trigger_raw = hydrator.get("trigger_per_hour")
    trigger = trigger_raw if isinstance(trigger_raw, dict) else {}
    failure_raw = hydrator.get("failure_rate")
    failure = failure_raw if isinstance(failure_raw, dict) else {}
    watchlist_raw = raw.get("watchlist")
    watchlist = watchlist_raw if isinstance(watchlist_raw, dict) else {}

    parsed["hydrator"]["triggerPerHour"]["warning"] = _coerce_float(
        trigger.get("warning"),
        parsed["hydrator"]["triggerPerHour"]["warning"],
    )
    parsed["hydrator"]["triggerPerHour"]["critical"] = _coerce_float(
        trigger.get("critical"),
        parsed["hydrator"]["triggerPerHour"]["critical"],
    )
    parsed["hydrator"]["failureRate"]["warning"] = _coerce_float(
        failure.get("warning"),
        parsed["hydrator"]["failureRate"]["warning"],
    )
    parsed["hydrator"]["failureRate"]["critical"] = _coerce_float(
        failure.get("critical"),
        parsed["hydrator"]["failureRate"]["critical"],
    )

    extras_input: list[JsonValue] = []
    raw_extras = watchlist.get("extras")
    if isinstance(raw_extras, list):
        extras_input.extend(raw_extras)
    legacy_extras = raw.get("watchlistExtras")
    if isinstance(legacy_extras, list):
        extras_input.extend(legacy_extras)
    legacy_payload_raw = raw.get("payload")
    legacy_payload = legacy_payload_raw if isinstance(legacy_payload_raw, dict) else {}
    kb_raw = legacy_payload.get("kb")
    payload_override = kb_raw if isinstance(kb_raw, dict) else None
    if payload_override:
        extras_input = [
            {"key": "payloadKb", **payload_override} if entry == "payloadKb" else entry for entry in extras_input
        ]
    if not extras_input and payload_override:
        extras_input.append({"key": "payloadKb", **payload_override})
    if not extras_input:
        extras_input = cast(list[JsonValue], json.loads(json.dumps(DEFAULT_GUIDELINES["watchlist"]["extras"])))

    normalized_extras: list[WatchlistExtra] = []
    for entry in extras_input:
        if entry is None:
            continue
        if isinstance(entry, str) and not entry.strip():
            continue
        if isinstance(entry, Mapping):
            normalized_extras.append(_normalize_watch_extra(cast(Mapping[str, JsonValue], entry)))
            continue
        if isinstance(entry, str):
            normalized_extras.append(_normalize_watch_extra(entry))
    parsed["watchlist"] = {
        "limit": max(1, int(watchlist.get("limit", parsed["watchlist"]["limit"]))),
        "extras": normalized_extras,
    }

    auto_snapshot_raw = raw.get("auto_snapshot")
    if isinstance(auto_snapshot_raw, dict):
        auto_snapshot = auto_snapshot_raw
        interval = max(0, int(auto_snapshot.get("interval_seconds", 0)))
        mode = str(auto_snapshot.get("mode", "console") or "console").lower()
        destination = str(auto_snapshot.get("destination", "")).strip().lower()
        if destination not in {"console", "clipboard", "api"}:
            destination = "api" if mode == "clipboard" else "console"
        retention = max(0, int(auto_snapshot.get("retention_minutes", 0)))
        parsed["autoSnapshot"] = {
            "intervalSeconds": interval,
            "mode": "clipboard" if mode == "clipboard" else "console",
            "destination": destination,
            "retentionMinutes": retention,
        }

    return parsed


def _safe_load_yaml(path: Path) -> Mapping[str, JsonValue] | None:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except FileNotFoundError:
        return None
    except yaml.YAMLError as exc:  # pragma: no cover - configuration errors logged for operators
        logger.warning("Failed to parse live diagnostics config %s: %s", path, exc)
        return None
    if isinstance(data, Mapping):
        return cast(Mapping[str, JsonValue], data)
    logger.warning("Live diagnostics config %s must be a mapping", path)
    return None


def _candidate_paths(preferred: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if preferred:
        candidates.append(preferred)
    env_override = os.getenv("SHOGI_ARENA_LIVE_DIAGNOSTICS_CONFIG")
    if env_override:
        candidates.append(Path(env_override))
    candidates.append(Path(".sandbox/configs/run/live_diagnostics.yml"))
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
    return _deep_copy_guidelines()
