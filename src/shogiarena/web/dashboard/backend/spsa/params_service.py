"""SPSA parameter metadata service."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import Any

from shogiarena.arena.tuning.param_io import read_params

from .store import SpsaStore

logger = logging.getLogger(__name__)


class SpsaParamsService:
    """Service for loading and summarising SPSA parameter definitions."""

    def __init__(self, *, store: SpsaStore, run_dir: Path | None) -> None:
        self._store = store
        self._run_dir = run_dir
        self._cache_lock = Lock()
        self._cached_params_path: Path | None = None
        self._cached_params_signature: tuple[int, int] | None = None
        self._cached_entries: list[dict[str, Any]] | None = None

    def load_variant_entry(self, variant_id: str) -> dict[str, Any] | None:
        """Return variant entry from ``engine_variants.json`` if available."""

        variants = self._store.load_variants_map()
        entry = variants.get(variant_id)
        return entry if isinstance(entry, dict) else None

    def build_params_payload(self) -> dict[str, Any]:
        """Build the parameter summary payload used by the API."""

        meta_start = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - meta_start) * 1000.0
        params_path = self._resolve_params_path(meta_data)
        initial_params = self._extract_initial_params(meta_data)
        params_start = time.perf_counter()
        entries, from_cache = self._load_params_entries(params_path)
        params_elapsed = (time.perf_counter() - params_start) * 1000.0 if not from_cache else 0.0

        if meta_elapsed >= 50.0:
            print(f"[spsa:params:meta] duration_ms={meta_elapsed:.1f}", flush=True)
        if params_elapsed >= 50.0:
            print(f"[spsa:params:read] duration_ms={params_elapsed:.1f} path={params_path}", flush=True)

        values_map = {entry["name"]: float(entry["v"]) for entry in entries}

        if not initial_params:
            initial_params = dict(values_map)
        else:
            for entry in entries:
                initial_params.setdefault(entry["name"], float(entry["v"]))

        # Use update_idx-based variant_id (v000001 format) instead of hash
        normalized_variant_id = None

        num_params = len(entries)
        num_used = sum(1 for entry in entries if not entry.get("not_used", False))
        epsilon = 1e-9
        num_clamped = sum(
            1
            for entry in entries
            if (
                abs(float(entry.get("v", 0.0)) - float(entry.get("min", 0.0))) <= epsilon
                or abs(float(entry.get("v", 0.0)) - float(entry.get("max", 0.0))) <= epsilon
            )
        )
        clamped_ratio = (num_clamped / num_params) if num_params else None

        diffs: dict[str, float] | None = None
        initial_variant_id: str | None = None
        if initial_params:
            diffs = {
                entry["name"]: float(entry["v"]) - float(initial_params.get(entry["name"], entry["v"]))
                for entry in entries
            }
            # Use update_idx-based variant_id (v000001 format) instead of hash
            initial_variant_id = None

        return {
            "params": entries,
            "variant_id": normalized_variant_id,
            "num_params": num_params,
            "num_used": num_used,
            "num_clamped": num_clamped,
            "clamped_ratio": clamped_ratio,
            "initial_params": initial_params or None,
            "diffs": diffs,
            "initial_variant_id": initial_variant_id,
        }

    def _resolve_params_path(self, meta_data: dict[str, Any] | None) -> Path | None:
        candidates: list[Path] = []
        if isinstance(meta_data, dict):
            raw_path = meta_data.get("parameters_path")
            if isinstance(raw_path, str) and raw_path.strip():
                candidates.append(Path(raw_path))

        candidates.extend(self._candidate_param_paths())

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _candidate_param_paths(self) -> list[Path]:
        if self._run_dir is None:
            return []

        search_roots = [
            self._run_dir / "spsa" / "params",
            self._run_dir / "spsa",
            self._run_dir,
        ]
        seen: set[Path] = set()
        candidates: list[Path] = []

        for root in search_roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.glob("*.params"):
                if path not in seen:
                    candidates.append(path)
                    seen.add(path)
        return candidates

    @staticmethod
    def _extract_initial_params(meta_data: dict[str, Any] | None) -> dict[str, float]:
        if not isinstance(meta_data, dict):
            return {}
        raw = meta_data.get("initial_params")
        if not isinstance(raw, dict):
            return {}

        initial_params: dict[str, float] = {}
        for key, value in raw.items():
            try:
                initial_params[str(key)] = float(value)
            except (TypeError, ValueError):
                logger.debug("Skipping non-numeric initial param %s=%r", key, value)
        return initial_params

    def _read_params_entries(self, params_path: Path | None) -> list[dict[str, Any]]:
        if params_path is None:
            return []

        try:
            start = time.perf_counter()
            entries = read_params(params_path)
            elapsed = (time.perf_counter() - start) * 1000.0
            if elapsed >= 50.0:
                print(f"[spsa:params:parse] duration_ms={elapsed:.1f} path={params_path}", flush=True)
        except FileNotFoundError:
            logger.warning("Parameters file not found: %s", params_path)
            return []
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load parameters from %s: %s", params_path, exc, exc_info=exc)
            return []

        formatted: list[dict[str, Any]] = []
        for entry in entries:
            formatted.append(
                {
                    "name": entry.name,
                    "type": entry.type,
                    "v": float(entry.v),
                    "min": float(entry.min),
                    "max": float(entry.max),
                    "step": float(entry.step),
                    "delta": float(entry.delta),
                    "comment": entry.comment,
                    "not_used": bool(entry.not_used),
                }
            )
        return formatted

    def _load_params_entries(self, params_path: Path | None) -> tuple[list[dict[str, Any]], bool]:
        signature = self._stat_signature(params_path)
        with self._cache_lock:
            if (
                params_path is not None
                and signature is not None
                and self._cached_params_path == params_path
                and self._cached_params_signature == signature
                and self._cached_entries is not None
            ):
                return self._clone_entries(self._cached_entries), True

        entries = self._read_params_entries(params_path)
        cache_ready = self._clone_entries(entries)
        with self._cache_lock:
            if params_path is not None and signature is not None:
                self._cached_params_path = params_path
                self._cached_params_signature = signature
                self._cached_entries = cache_ready
            else:
                self._cached_params_path = None
                self._cached_params_signature = None
                self._cached_entries = cache_ready
        return entries, False

    @staticmethod
    def _clone_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(entry) for entry in entries]

    @staticmethod
    def _stat_signature(path: Path | None) -> tuple[int, int] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size
