"""Persist and load structured run failure records."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

from shogiarena._core.contexts.game_session.domain.failure_records import (
    FailurePhase,
    RunFailureRecord,
    coerce_failure_phase,
)
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str

logger = logging.getLogger(__name__)

_FAILURES_JSON = "run_failures.json"
_FAILURES_JSONL = "run_failures.jsonl"
_STARTUP_FAILURE_JSON = "engine_startup_failure.json"
_FAILURES_DIR = "failures"


class RunFailureRecordService:
    """run failure records を JSON アーティファクトとして保存する。"""

    def append_failure(self, *, run_dir: Path, record: RunFailureRecord) -> None:
        failure_dir = run_dir / _FAILURES_DIR
        failure_dir.mkdir(parents=True, exist_ok=True)
        payload = record.to_payload()
        records = [*self.load_failures(run_dir), payload]
        self._append_jsonl(failure_dir / _FAILURES_JSONL, payload)
        self._write_snapshot(failure_dir / _FAILURES_JSON, records)
        if record.failure_phase in {"engine_start", "isready"}:
            startup_records = [record for record in records if _is_startup_failure_payload(record)]
            self._write_snapshot(failure_dir / _STARTUP_FAILURE_JSON, startup_records)

    def load_failures(self, run_dir: Path) -> list[JsonObject]:
        failure_dir = run_dir / _FAILURES_DIR
        snapshot_path = failure_dir / _FAILURES_JSON
        if snapshot_path.exists():
            loaded = self._load_snapshot(snapshot_path)
            if loaded is not None:
                return loaded
        return self._load_jsonl(failure_dir / _FAILURES_JSONL)

    @staticmethod
    def build_record_from_exception(
        *,
        exc: BaseException,
        game_id: str | None,
        scheduled_black_engine: str | None,
        scheduled_white_engine: str | None,
        fallback_phase: FailurePhase = "unknown",
        log_artifact_path: str | None = None,
    ) -> RunFailureRecord:
        phase = fallback_phase
        engine = None
        diagnostic: JsonObject | None = None
        diagnostic_payload = getattr(exc, "diagnostic_payload", None)
        if callable(diagnostic_payload):
            phase = coerce_failure_phase(coerce_str(getattr(exc, "failure_phase", None)) or fallback_phase)
            engine = coerce_str(getattr(exc, "engine_name", None))
            raw_diagnostic = diagnostic_payload()
            if isinstance(raw_diagnostic, Mapping):
                diagnostic = coerce_json_object_serialized(raw_diagnostic, field_name="diagnostic")
        return RunFailureRecord(
            game_id=game_id,
            scheduled_black_engine=scheduled_black_engine,
            scheduled_white_engine=scheduled_white_engine,
            failure_phase=phase,
            exception_class=type(exc).__name__,
            short_message=str(exc),
            engine=engine,
            log_artifact_path=log_artifact_path,
            diagnostic=diagnostic,
        )

    @staticmethod
    def failure_counts_by_phase(records: Iterable[Mapping[str, JsonValue]]) -> JsonObject:
        counts: dict[str, int] = {}
        for record in records:
            phase = coerce_failure_phase(coerce_str(record.get("failure_phase")))
            counts[phase] = counts.get(phase, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _append_jsonl(path: Path, payload: JsonObject) -> None:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            logger.exception("Failed to append run failure record to %s: %s", path, exc)

    @staticmethod
    def _write_snapshot(path: Path, records: list[JsonObject]) -> None:
        payload: JsonObject = {
            "schema_version": 1,
            "failures": records,
        }
        try:
            write_json_atomic(path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to write run failure snapshot to %s: %s", path, exc)

    @staticmethod
    def _load_snapshot(path: Path) -> list[JsonObject] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed to load run failure snapshot from %s: %s", path, exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        failures = raw.get("failures")
        if not isinstance(failures, list):
            return None
        records: list[JsonObject] = []
        for item in failures:
            if isinstance(item, Mapping):
                records.append(coerce_json_object_serialized(item, field_name="failures[]"))
        return records

    @staticmethod
    def _load_jsonl(path: Path) -> list[JsonObject]:
        if not path.exists():
            return []
        records: list[JsonObject] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.debug("Failed to load run failure jsonl from %s: %s", path, exc)
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping invalid run failure jsonl line from %s", path)
                continue
            if isinstance(raw, Mapping):
                records.append(coerce_json_object_serialized(raw, field_name="run_failures.jsonl"))
        return records


def _is_startup_failure_payload(record: Mapping[str, JsonValue]) -> bool:
    phase = coerce_str(record.get("failure_phase"))
    return phase in {"engine_start", "isready"}


__all__ = ["RunFailureRecordService"]
