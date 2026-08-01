"""SPSA data storage layer for reading from files and database."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TypeGuard

import yaml

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import UpdateEntry
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none as _as_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

from .event_types import SpsaEvent, parse_spsa_event
from .io_models.index_io_models import IndexData, IndexMetadata
from .io_models.meta_io_models import SpsaMetaData

logger = logging.getLogger(__name__)


class SpsaStore(DashboardSpsaStorePort):
    """Data access layer for SPSA-related files and database operations."""

    @staticmethod
    def _is_update_entry(value: Mapping[str, JsonValue]) -> TypeGuard[UpdateEntry]:
        return isinstance(value.get("update_idx"), int)

    def __init__(
        self,
        *,
        run_dir: Path,
    ) -> None:
        self._run_dir = run_dir

    def spsa_path(self, filename: str) -> Path:
        """Get path to a file in the SPSA directory."""
        return self._run_dir / "spsa" / filename

    def run_path(self, filename: str) -> Path:
        """Get path to a file in the run directory."""
        return self._run_dir / filename

    @staticmethod
    def load_json_file(path: Path) -> JsonValue | None:
        """Load JSON file, returning None on failure."""
        try:
            return json_serialize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load JSON from %s: %s", path, exc)
            return None

    def load_json_lines(self, path: Path) -> list[JsonObject]:
        """Load JSONL file, returning list of dict entries."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return []

        entries: list[JsonObject] = []
        for line in lines:
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSON line in %s: %s", path, exc)
                continue
            payload_obj = _as_json_object(payload)
            if payload_obj is not None:
                entries.append(payload_obj)
        return entries

    def load_meta_data(self) -> SpsaMetaData:
        """Load meta.json as a typed Pydantic model."""
        meta_path = self.spsa_path("meta.json")
        if not meta_path.exists():
            return SpsaMetaData()
        raw_meta = self.load_json_file(meta_path)
        if not isinstance(raw_meta, dict):
            return SpsaMetaData()
        return SpsaMetaData.model_validate(raw_meta)

    def current_session_uuid(self) -> str | None:
        """Get current session UUID from meta.json."""
        return self.load_meta_data().session_uuid

    def load_variants_map(self) -> JsonObject:
        """Load engine_variants.json."""
        sidecar = self.spsa_path("engine_variants.json")
        if not sidecar.exists():
            return {}
        data = self.load_json_file(sidecar)
        data_map = _as_json_object(data)
        if data_map is None:
            return {}
        return data_map

    def load_index_updates(self) -> list[UpdateEntry]:
        """Load updates from index.json (without enrichment)."""
        index_path = self.spsa_path("index.json")
        if not index_path.exists():
            return []
        raw = self.load_json_file(index_path)
        if not isinstance(raw, dict):
            return []
        parsed = IndexData.model_validate(raw)
        updates: list[UpdateEntry] = []
        for update in parsed.updates:
            update_obj = _as_json_object(update.to_update_entry_dict())
            if update_obj is not None and self._is_update_entry(update_obj):
                updates.append(update_obj)
        return updates

    def load_index_metadata(self) -> IndexMetadata:
        """Load metadata section from index.json as a typed Pydantic model."""
        index_path = self.spsa_path("index.json")
        if not index_path.exists():
            return IndexMetadata()
        index_data = self.load_json_file(index_path)
        index_map = _as_json_object(index_data)
        if index_map is None:
            return IndexMetadata()
        raw_meta = index_map.get("metadata")
        meta_map = _as_json_object(raw_meta)
        if meta_map is None:
            return IndexMetadata()
        return IndexMetadata.model_validate(meta_map)

    def load_event_entries(self) -> list[SpsaEvent]:
        """Load the cumulative run-wide events.jsonl history.

        各エントリは読み込み境界で ``parse_spsa_event`` により型安全に正規化される。
        """
        events_path = self.spsa_path("events.jsonl")
        if not events_path.exists():
            return []
        raw_entries = self.load_json_lines(events_path)
        return [parse_spsa_event(entry) for entry in raw_entries]

    def load_ltc_results(self) -> list[SpsaEvent]:
        """Load the cumulative run-wide LTC result history."""
        results_path = self.spsa_path("ltc/results.jsonl")
        if not results_path.exists():
            return []
        raw_entries = self.load_json_lines(results_path)
        return [parse_spsa_event(entry) for entry in raw_entries]

    @staticmethod
    def load_yaml_file(path: Path) -> JsonValue | None:
        """Load YAML file, returning None on failure."""
        try:
            return json_serialize(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
            logger.warning("Failed to load YAML from %s: %s", path, exc)
            return None

    def read_spsa_engine_names(self) -> tuple[str | None, str | None]:
        """Read baseline and tuned engine names from YAML files."""
        base_path = self.spsa_path("engine_baseline.yaml")
        tuned_path = self.spsa_path("engine_tuned.yaml")
        base_name: str | None = None
        tuned_name: str | None = None
        if base_path.exists():
            data = self.load_yaml_file(base_path)
            data_map = _as_json_object(data)
            if data_map is not None:
                raw_name = data_map.get("name")
                if isinstance(raw_name, str):
                    base_name = raw_name
        if tuned_path.exists():
            data = self.load_yaml_file(tuned_path)
            data_map = _as_json_object(data)
            if data_map is not None:
                raw_name = data_map.get("name")
                if isinstance(raw_name, str):
                    tuned_name = raw_name
        return base_name, tuned_name
