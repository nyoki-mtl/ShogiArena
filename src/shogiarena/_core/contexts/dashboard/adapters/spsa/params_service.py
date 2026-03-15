"""SPSA parameter metadata service."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import cast

from shogiarena._core.contexts.dashboard.application.spsa.io_models.meta_io_models import SpsaMetaData
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import ParamEntry, ParamsPayload
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.params_file_loader import (
    read_params_file,
    resolve_params_path,
)
from shogiarena._core.contexts.spsa.application.dashboard.params_payload_builder import (
    build_params_payload as build_params_payload_use_case,
)
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class SpsaParamsService:
    """Service for loading and summarising SPSA parameter definitions."""

    def __init__(self, *, store: DashboardSpsaStorePort, run_dir: Path) -> None:
        self._store = store
        self._run_dir = run_dir
        self._cache_lock = Lock()
        self._cached_params_path: Path | None = None
        self._cached_params_signature: tuple[int, int] | None = None
        self._cached_entries: list[ParamEntry] | None = None

    def load_variant_entry(self, variant_id: str) -> JsonObject | None:
        """Return variant entry from ``engine_variants.json`` if available."""

        variants = self._store.load_variants_map()
        entry = variants.get(variant_id)
        if not isinstance(entry, dict):
            return None
        return dict(entry)

    def build_params_payload(self) -> ParamsPayload:
        """Build the parameter summary payload used by the API."""

        meta_start = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - meta_start) * 1000.0
        params_path = resolve_params_path(parameters_path=meta_data.parameters_path, run_dir=self._run_dir)
        initial_params = self._extract_initial_params(meta_data)
        params_start = time.perf_counter()
        entries, from_cache = self._load_params_entries(params_path)
        params_elapsed = (time.perf_counter() - params_start) * 1000.0 if not from_cache else 0.0

        if meta_elapsed >= 50.0:
            print(f"[spsa:params:meta] duration_ms={meta_elapsed:.1f}", flush=True)
        if params_elapsed >= 50.0:
            print(f"[spsa:params:read] duration_ms={params_elapsed:.1f} path={params_path}", flush=True)
        payload = build_params_payload_use_case(
            entries=entries,
            initial_params=initial_params,
        )
        return cast(ParamsPayload, payload)

    @staticmethod
    def _extract_initial_params(meta_data: SpsaMetaData) -> dict[str, float]:
        return dict(meta_data.initial_params)

    def _read_params_entries(self, params_path: Path | None) -> list[ParamEntry]:
        if params_path is None:
            return []

        try:
            start = time.perf_counter()
            entries = read_params_file(params_path)
            elapsed = (time.perf_counter() - start) * 1000.0
            if elapsed >= 50.0:
                print(f"[spsa:params:parse] duration_ms={elapsed:.1f} path={params_path}", flush=True)
        except FileNotFoundError:
            logger.warning("Parameters file not found: %s", params_path)
            return []
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load parameters from %s: %s", params_path, exc, exc_info=exc)
            return []

        formatted: list[ParamEntry] = []
        for entry in entries:
            formatted.append(
                ParamEntry(
                    name=entry.name,
                    type=entry.type,
                    v=entry.value,
                    min=entry.min,
                    max=entry.max,
                    step=entry.step,
                    delta=entry.delta,
                    comment=entry.comment,
                    is_not_used=entry.is_not_used,
                )
            )
        return formatted

    def _load_params_entries(self, params_path: Path | None) -> tuple[list[ParamEntry], bool]:
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
    def _clone_entries(entries: list[ParamEntry]) -> list[ParamEntry]:
        return [
            ParamEntry(
                name=entry["name"],
                type=entry["type"],
                v=entry["v"],
                min=entry["min"],
                max=entry["max"],
                step=entry["step"],
                delta=entry["delta"],
                comment=entry["comment"],
                is_not_used=entry["is_not_used"],
            )
            for entry in entries
        ]

    @staticmethod
    def _stat_signature(path: Path | None) -> tuple[int, int] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size
