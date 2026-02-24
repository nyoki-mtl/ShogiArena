"""SPSA data storage layer for reading from files and database."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from shogiarena.arena.orchestrators.spsa.event_types import SpsaEvent, parse_spsa_event
from shogiarena.db import ShogiRepository

from .models import IndexData, IndexMetadata, SpsaMetaData
from .types import UpdateEntry

logger = logging.getLogger(__name__)


class SpsaStore:
    """Data access layer for SPSA-related files and database operations."""

    def __init__(
        self,
        *,
        run_dir: Path | None,
        db_path: Path,
        ensure_shogidb: Callable[[], None],
        shogidb_supplier: Callable[[], ShogiRepository | None],
    ) -> None:
        self._run_dir = run_dir
        self._db_path = db_path
        self._ensure_shogidb = ensure_shogidb
        self._get_shogidb = shogidb_supplier

    def spsa_path(self, filename: str) -> Path | None:
        """Get path to a file in the SPSA directory."""
        if not self._run_dir:
            return None
        return self._run_dir / "spsa" / filename

    @staticmethod
    def load_json_file(path: Path) -> Any | None:  # I/O boundary: 任意の JSON 構造を読み込む
        """Load JSON file, returning None on failure."""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load JSON from %s: %s", path, exc)
            return None

    def load_json_lines(self, path: Path) -> list[dict[str, Any]]:
        """Load JSONL file, returning list of dict entries."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return []

        entries: list[dict[str, Any]] = []
        for line in lines:
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSON line in %s: %s", path, exc)
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def load_meta_data(self) -> SpsaMetaData:
        """Load meta.json as a typed Pydantic model."""
        meta_path = self.spsa_path("meta.json")
        if not meta_path or not meta_path.exists():
            return SpsaMetaData()
        raw_meta = self.load_json_file(meta_path)
        if not isinstance(raw_meta, dict):
            return SpsaMetaData()
        return SpsaMetaData.model_validate(raw_meta)

    def current_session_uuid(self) -> str | None:
        """Get current session UUID from meta.json."""
        return self.load_meta_data().session_uuid

    def load_schedule_entries(self) -> list[dict[str, Any]]:
        """Load game_schedule.json from run_dir."""
        if not self._run_dir:
            return []
        schedule_path = self._run_dir / "game_schedule.json"
        if not schedule_path.exists():
            return []
        raw_schedule = self.load_json_file(schedule_path)
        if not isinstance(raw_schedule, list):
            return []
        return [entry for entry in raw_schedule if isinstance(entry, dict)]

    def load_variants_map(self) -> dict[str, Any]:
        """Load engine_variants.json."""
        sidecar = self.spsa_path("engine_variants.json")
        if not sidecar or not sidecar.exists():
            return {}
        data = self.load_json_file(sidecar)
        return data if isinstance(data, dict) else {}

    def load_index_updates(self) -> list[UpdateEntry]:
        """Load updates from index.json (without enrichment)."""
        index_path = self.spsa_path("index.json")
        if not index_path or not index_path.exists():
            return []
        if self._has_gradients_or_deltas():
            logger.debug("Ignoring legacy index.json because gradients/deltas are present in %s", self._run_dir)
            return []
        raw = self.load_json_file(index_path)
        if not isinstance(raw, dict):
            return []
        parsed = IndexData.model_validate(raw)
        return cast(list[UpdateEntry], [e.to_update_entry_dict() for e in parsed.updates])

    def load_index_metadata(self) -> IndexMetadata:
        """Load metadata section from index.json as a typed Pydantic model."""
        index_path = self.spsa_path("index.json")
        if not index_path or not index_path.exists():
            return IndexMetadata()
        index_data = self.load_json_file(index_path)
        if not isinstance(index_data, dict):
            return IndexMetadata()
        raw_meta = index_data.get("metadata")
        if not isinstance(raw_meta, dict):
            return IndexMetadata()
        return IndexMetadata.model_validate(raw_meta)

    def load_event_entries(self) -> list[SpsaEvent]:
        """Load events.jsonl, scoped by current session UUID.

        各エントリは読み込み境界で ``parse_spsa_event`` により型安全に正規化される。
        """
        events_path = self.spsa_path("events.jsonl")
        if not events_path or not events_path.exists():
            return []
        raw_entries = self.load_json_lines(events_path)
        session_uuid = self.current_session_uuid()
        if session_uuid:
            raw_entries = [e for e in raw_entries if str(e.get("session_uuid") or "").strip() == session_uuid]
        return [parse_spsa_event(entry) for entry in raw_entries]

    def _has_gradients_or_deltas(self) -> bool:
        grad_dir = self.spsa_path("gradients")
        delta_dir = self.spsa_path("deltas")
        return bool((grad_dir and grad_dir.exists()) or (delta_dir and delta_dir.exists()))

    def load_ltc_results(self) -> list[SpsaEvent]:
        """Load ltc/results.jsonl, scoped by current session UUID."""
        results_path = self.spsa_path("ltc/results.jsonl")
        if not results_path or not results_path.exists():
            return []
        raw_entries = self.load_json_lines(results_path)
        session_uuid = self.current_session_uuid()
        if session_uuid:
            raw_entries = [e for e in raw_entries if str(e.get("session_uuid") or "").strip() == session_uuid]
        return [parse_spsa_event(entry) for entry in raw_entries]

    @staticmethod
    def load_yaml_file(path: Path) -> Any | None:
        """Load YAML file, returning None on failure."""
        try:
            import yaml  # Local import to avoid unconditional dependency during tests
        except ImportError as exc:
            logger.warning("Failed to import yaml library: %s", exc)
            return None
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
            logger.warning("Failed to load YAML from %s: %s", path, exc)
            return None

    def read_spsa_engine_names(self) -> tuple[str | None, str | None]:
        """Read baseline and tuned engine names from YAML files."""
        base_path = self.spsa_path("engine_baseline.yaml")
        tuned_path = self.spsa_path("engine_tuned.yaml")
        base_name = tuned_name = None
        if base_path and base_path.exists():
            data = self.load_yaml_file(base_path)
            base_name = data.get("name") if isinstance(data, dict) else None
        if tuned_path and tuned_path.exists():
            data = self.load_yaml_file(tuned_path)
            tuned_name = data.get("name") if isinstance(data, dict) else None
        return base_name, tuned_name

    def get_shogidb(self) -> ShogiRepository | None:
        """Get ShogiRepository instance, ensuring it's initialized."""
        self._ensure_shogidb()
        return self._get_shogidb()

    def get_db_path(self) -> Path:
        """Get database path."""
        return self._db_path

    def save_best_params_snapshot(
        self,
        *,
        variant_token: str,
        update_idx: int,
        params: Mapping[str, float],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a snapshot of accepted best parameters into run_dir."""
        if not self._run_dir:
            return
        normalized_token = variant_token.strip()
        if not normalized_token:
            return
        try:
            safe_update_idx = int(update_idx)
        except (TypeError, ValueError):
            return

        target_dir = self.spsa_path("best_params")
        if target_dir is None:
            return
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Failed to create best_params directory at %s: %s", target_dir, exc)
            return

        snapshot_path = target_dir / f"{normalized_token}.json"
        if snapshot_path.exists():
            return

        sanitized_params: dict[str, float] = {}
        for key, value in params.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if not (numeric == numeric and abs(numeric) != float("inf")):  # NaN or inf
                continue
            sanitized_params[str(key)] = numeric

        if not sanitized_params:
            return

        payload: dict[str, Any] = {
            "variant": normalized_token,
            "update_idx": safe_update_idx,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "params": sanitized_params,
        }
        if metadata:
            payload["metadata"] = dict(metadata)

        try:
            snapshot_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Failed to write best params snapshot to %s: %s", snapshot_path, exc)
