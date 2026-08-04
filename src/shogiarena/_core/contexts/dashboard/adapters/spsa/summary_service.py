"""SPSA summary calculation service with incremental caching."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import cast

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import SpsaSummaryPayload
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.summary_cache_state import (
    create_empty_summary_cache_state,
    refresh_summary_cache_state_from_events,
)
from shogiarena._core.contexts.spsa.application.dashboard.summary_payload_builder import (
    build_summary_payload as build_summary_payload_use_case,
)
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256
from shogiarena._core.shared.kernel.run_manifest_reader import parse_run_manifest, sealed_manifest_resume_hash
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int

_STORE_LOG_THRESHOLD_MS = 50.0
logger = logging.getLogger(__name__)
LedgerSummaryLoader = Callable[[], JsonObject]
OperationalStatusLoader = Callable[[], JsonObject]


@dataclass(frozen=True, slots=True)
class _LoadedArtifact:
    """1 回の読み取りで得たバイト列の content key と、その解析結果。

    ``content_key`` はバイト列の sha256。読み取りに失敗した場合のみ ``None`` になる。
    解析に失敗した場合は ``content_key`` が残り ``parsed`` だけが ``None`` になる。

    status の判定と canonical digest の計算を**同一のバイト列**に対して行うために、
    両者を 1 つの値として持ち回る。
    """

    content_key: str | None
    parsed: JsonObject | None


class SpsaSummaryService:
    """Service for computing SPSA summary statistics with incremental caching."""

    def __init__(
        self,
        store: DashboardSpsaStorePort,
        *,
        ledger_summary_loader: LedgerSummaryLoader | None = None,
        operational_status_loader: OperationalStatusLoader | None = None,
    ) -> None:
        self._store = store
        self._ledger_summary_loader = ledger_summary_loader
        self._operational_status_loader = operational_status_loader
        self._lock = Lock()
        self._cache_state = create_empty_summary_cache_state()
        self._artifact_cache_lock = Lock()
        self._artifact_cache: dict[Path, tuple[str, JsonObject | None]] = {}
        self._digest_cache: dict[Path, tuple[str, str]] = {}
        self._manifest_cache: tuple[str, JsonObject | None] | None = None

    def compute_summary(self) -> SpsaSummaryPayload:
        """Compute summary statistics from cached aggregates and new events."""

        start_meta = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - start_meta) * 1000.0

        with self._lock:
            if self._ledger_summary_loader is not None:
                aggregate_values = self._ledger_summary_loader()
            else:
                refresh_summary_cache_state_from_events(
                    self._cache_state,
                    events_path=self._store.spsa_path("events.jsonl"),
                    logger=logger,
                )
                aggregate_values = self._cache_state.aggregates.to_dict()
            updates_seen = aggregate_values.get("updates_seen")
            updates_completed = len(updates_seen) if isinstance(updates_seen, list) else 0
            step_history = aggregate_values.get("step_history")
            update_timestamps = aggregate_values.get("update_timestamps")
            engine_stats = {
                name: {"wins": stat.wins, "losses": stat.losses, "draws": stat.draws, "games": stat.games}
                for name, stat in meta_data.engine_stats.items()
            }
            operational_status = self._build_operational_status(meta_data.session_uuid)
            summary = build_summary_payload_use_case(
                experiment_name=meta_data.experiment_name,
                num_updates_total=meta_data.effective_num_updates,
                wins=coerce_int(aggregate_values.get("wins")) or 0,
                losses=coerce_int(aggregate_values.get("losses")) or 0,
                draws=coerce_int(aggregate_values.get("draws")) or 0,
                tuned_black_wins=coerce_int(aggregate_values.get("tuned_black_wins")) or 0,
                tuned_black_losses=coerce_int(aggregate_values.get("tuned_black_losses")) or 0,
                tuned_white_wins=coerce_int(aggregate_values.get("tuned_white_wins")) or 0,
                tuned_white_losses=coerce_int(aggregate_values.get("tuned_white_losses")) or 0,
                updates_completed=updates_completed,
                last_update_idx=coerce_int(aggregate_values.get("last_update_idx")),
                step_history=(
                    [value for item in step_history if (value := coerce_float(item)) is not None]
                    if isinstance(step_history, list)
                    else []
                ),
                last_delta_norm=coerce_float(aggregate_values.get("last_delta_norm")),
                update_timestamps=(
                    [value for item in update_timestamps if (value := coerce_int(item)) is not None]
                    if isinstance(update_timestamps, list)
                    else []
                ),
                engine_time_controls=meta_data.engine_time_controls,
                default_time_control=meta_data.default_time_control,
                engines=meta_data.engines,
                engine_instances=meta_data.engine_instances,
                engine_stats=engine_stats,
                engines_meta=meta_data.engines_meta,
                spsa_config=meta_data.resolve_spsa_config(),
                current_session_uuid=meta_data.session_uuid,
                resume_boundaries=[session.model_dump(mode="json") for session in meta_data.sessions],
                operational_status=operational_status,
            )

        if meta_elapsed >= _STORE_LOG_THRESHOLD_MS:
            print(f"[spsa:store:meta] duration_ms={meta_elapsed:.1f}", flush=True)

        return cast(SpsaSummaryPayload, summary)

    def _build_operational_status(self, session_id: str | None) -> JsonObject:
        ledger_status = self._operational_status_loader() if self._operational_status_loader is not None else {}
        payload: JsonObject = dict(ledger_status)
        payload["session_id"] = session_id

        manifest = self._load_manifest()
        manifest_status = str(manifest.get("status")) if manifest is not None and manifest.get("status") else "unknown"
        manifest_schema = manifest.get("schema_version") if manifest is not None else None
        payload["manifest"] = {
            "status": manifest_status,
            "schema_version": manifest_schema,
            "resume_hash": sealed_manifest_resume_hash(manifest),
        }

        fixed_path = self._store.spsa_path("fixed_option_preflight.json")
        fixed_loaded = self._load_artifact(fixed_path)
        fixed = fixed_loaded.parsed
        scopes: list[str] = []
        fixed_engines = fixed.get("engines") if fixed is not None else None
        if isinstance(fixed_engines, list):
            for engine in fixed_engines:
                if not isinstance(engine, dict):
                    continue
                runtime = engine.get("runtime_evidence")
                if isinstance(runtime, dict) and isinstance(runtime.get("scope"), str):
                    scopes.append(runtime["scope"])
        payload["fixed_option_preflight"] = {
            "status": str(fixed.get("status")) if fixed is not None and fixed.get("status") else "unknown",
            "evidence_scopes": sorted(set(scopes)),
        }

        tunable_path = self._store.spsa_path("tunable_handshake.json")
        tunable_loaded = self._load_artifact(tunable_path)
        tunable = tunable_loaded.parsed
        manifest_inputs = manifest.get("inputs") if manifest is not None else None
        handshake_entry = manifest_inputs.get("spsa_tunable_handshake") if isinstance(manifest_inputs, dict) else None
        handshake_digest = handshake_entry.get("sha256") if isinstance(handshake_entry, dict) else None
        fixed_entry = manifest_inputs.get("spsa_fixed_option_preflight") if isinstance(manifest_inputs, dict) else None
        fixed_digest = fixed_entry.get("sha256") if isinstance(fixed_entry, dict) else None
        payload["tunable_manifest"] = {
            "status": str(tunable.get("status")) if tunable is not None and tunable.get("status") else "unknown",
            "runtime_scope": tunable.get("runtime_scope") if tunable is not None else None,
            "engine_name": tunable.get("engine_name") if tunable is not None else None,
            "evidence_digest": handshake_digest,
            "engine_digests": self._engine_digests(manifest),
        }

        completion = self._load_object(self._store.run_path("completion_status.json"))
        if "completion" not in payload:
            payload["completion"] = (
                {
                    "status": completion.get("status"),
                    "termination_reason": completion.get("termination_reason"),
                    "last_committed_update": completion.get("last_committed_update"),
                    "pending_stage": completion.get("pending_stage"),
                    "resumable": completion.get("resumable"),
                }
                if completion is not None
                else {
                    "status": "unknown",
                    "termination_reason": None,
                    "last_committed_update": None,
                    "pending_stage": None,
                    "resumable": None,
                }
            )

        ledger = payload.get("ledger")
        ledger_revision = ledger.get("revision") if isinstance(ledger, dict) else None
        fixed_integrity = self._artifact_digest_status(fixed_path, fixed_loaded, fixed_digest)
        tunable_integrity = self._artifact_digest_status(tunable_path, tunable_loaded, handshake_digest)
        health_status = (
            "healthy"
            if manifest_status == "provenance_sealed"
            and isinstance(ledger_revision, int)
            and fixed_integrity == "verified"
            and tunable_integrity == "verified"
            else ("degraded" if manifest_status != "unknown" or ledger is not None else "unknown")
        )
        payload["artifact_health"] = {
            "status": health_status,
            "revision": ledger_revision,
            "fixed_option_preflight": fixed_integrity,
            "tunable_handshake": tunable_integrity,
        }
        if "node_multiplier" not in payload:
            payload["node_multiplier"] = {"status": "unknown", "value": None}
        return payload

    def _load_artifact(self, path: Path) -> _LoadedArtifact:
        """Read a run artifact once, reusing the parse while its bytes are unchanged.

        summary は高頻度で叩かれるが、preflight / handshake の JSON パースと
        canonical digest 計算は run 中ずっと同じ結果になる。

        キャッシュ鍵に mtime と size を使うと、同じ tick に同じサイズで書き換えられた
        改竄を見逃す。artifact_health は integrity evidence なので、鍵は**実バイト列の
        digest**にする。読み込みとハッシュは毎回行い、高価なパースと canonical
        serialization だけを省く。

        content key を戻り値に含めるのは、同じ呼び出しの中で digest 側が
        ファイルを読み直さないためである（`_artifact_canonical_digest` 参照）。
        """

        raw = self._read_bytes(path)
        if raw is None:
            with self._artifact_cache_lock:
                self._artifact_cache.pop(path, None)
            return _LoadedArtifact(content_key=None, parsed=None)
        content_key = hashlib.sha256(raw).hexdigest()
        with self._artifact_cache_lock:
            cached = self._artifact_cache.get(path)
            if cached is not None and cached[0] == content_key:
                return _LoadedArtifact(content_key=content_key, parsed=cached[1])

        try:
            parsed = coerce_json_object_or_none(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        with self._artifact_cache_lock:
            self._artifact_cache[path] = (content_key, parsed)
        return _LoadedArtifact(content_key=content_key, parsed=parsed)

    def _load_object(self, path: Path) -> JsonObject | None:
        """Read a run artifact whose digest is not needed."""

        return self._load_artifact(path).parsed

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    @staticmethod
    def _engine_digests(manifest: JsonObject | None) -> JsonObject:
        engines = manifest.get("engines") if manifest is not None else None
        if not isinstance(engines, list):
            return {}
        digests: JsonObject = {}
        for engine in engines:
            if not isinstance(engine, dict) or not isinstance(engine.get("name"), str):
                continue
            bytes_hash = engine.get("bytes_hash")
            digest = bytes_hash.get("engine_binary_sha256") if isinstance(bytes_hash, dict) else None
            digests[engine["name"]] = digest if isinstance(digest, str) else None
        return digests

    def _artifact_digest_status(self, path: Path, loaded: _LoadedArtifact, expected_digest: object) -> str:
        artifact = loaded.parsed
        if artifact is None or not isinstance(expected_digest, str) or not expected_digest:
            return "missing"
        digest = self._artifact_canonical_digest(path, artifact, loaded.content_key)
        return "verified" if digest == expected_digest else "mismatch"

    def _artifact_canonical_digest(self, path: Path, artifact: JsonObject, content_key: str | None) -> str:
        """Return the canonical digest of bytes that were already read in this call.

        以前はここでファイルをもう一度読んでいた。status は 1 回目のバイト列、
        digest は 2 回目のバイト列から計算されるため、その間に書き換えが起きると
        `artifact_health` が矛盾した組み合わせを報告しうる TOCTOU 窓があった。
        呼び出し元が読んだ content key をそのまま使い、読み取りを 1 回に統一する。
        """

        if content_key is None:
            return canonical_sha256(artifact)
        with self._artifact_cache_lock:
            cached = self._digest_cache.get(path)
            if cached is not None and cached[0] == content_key:
                return cached[1]
        digest = canonical_sha256(artifact)
        with self._artifact_cache_lock:
            self._digest_cache[path] = (content_key, digest)
        return digest

    def _load_manifest(self) -> JsonObject | None:
        """Read ``manifest.json`` once, through the content-keyed artifact cache."""

        path = self._store.run_path("manifest.json")
        raw = self._read_bytes(path)
        if raw is None:
            with self._artifact_cache_lock:
                self._manifest_cache = None
            logger.info("Run manifest not found: %s", path)
            return None
        content_key = hashlib.sha256(raw).hexdigest()
        with self._artifact_cache_lock:
            cached = self._manifest_cache
            if cached is not None and cached[0] == content_key:
                return cached[1]
        manifest = parse_run_manifest(raw, source=path, logger=logger)
        with self._artifact_cache_lock:
            self._manifest_cache = (content_key, manifest)
        return manifest
