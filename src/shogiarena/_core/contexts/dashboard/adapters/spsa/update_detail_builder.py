"""Build detailed SPSA update payloads for dashboard APIs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from shogiarena._core.contexts.dashboard.adapters.db_repository import open_dashboard_repository
from shogiarena._core.contexts.dashboard.application.spsa.ltc_regression_detail_codec import parse_ltc_regression_detail
from shogiarena._core.contexts.dashboard.application.spsa.variant_resolution import (
    extract_variant_from_game_id,
    format_variant_label,
    resolve_variant_id,
)
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import GameBriefEntry, UpdateDetailResponse, WdlCounts
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.platform.db.store.entities import Game
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository import ShogiRepositoryPort
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)

GameEventSnapshotLoader = Callable[[str], JsonObject | None]


class _PhaseWdlPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phase_wdl: dict[str, dict[str, object]] = Field(default_factory=dict)


class _GameMetaPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    games_meta: dict[str, dict[str, object]] = Field(default_factory=dict)


def _parse_phase_wdl_payload(raw_value: JsonValue | None) -> dict[str, dict[str, object]]:
    if raw_value is None:
        return {}
    try:
        parsed = _PhaseWdlPayloadModel.model_validate({"phase_wdl": raw_value})
    except ValidationError as exc:
        logger.debug("Ignoring invalid phase_wdl payload: %s", exc)
        return {}
    return parsed.phase_wdl


def _parse_games_meta_payload(raw_value: JsonValue | None) -> dict[str, dict[str, object]]:
    if raw_value is None:
        return {}
    try:
        parsed = _GameMetaPayloadModel.model_validate({"games_meta": raw_value})
    except ValidationError as exc:
        logger.debug("Ignoring invalid games_meta payload: %s", exc)
        return {}
    return parsed.games_meta


def _as_optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _as_optional_int(value: JsonValue | None) -> int | None:
    return coerce_int(value)


def _counter_value(value: JsonValue | None) -> int:
    normalized = coerce_int(value)
    return normalized if normalized is not None else 0


class SpsaUpdateDetailBuilder:
    """Compose API payloads for a single update index."""

    def __init__(
        self,
        *,
        store: DashboardSpsaStorePort,
        db_path: Path,
        game_event_snapshot_loader: GameEventSnapshotLoader,
    ) -> None:
        self._store = store
        self._db_path = db_path
        self._load_game_event_snapshot = game_event_snapshot_loader

    def build(self, *, idx: int, detail_state: Mapping[str, JsonValue]) -> UpdateDetailResponse:
        params_obj = detail_state.get("params")
        params = dict(params_obj) if isinstance(params_obj, Mapping) else None
        variant_id = _as_optional_str(detail_state.get("variant_id"))
        wins = _counter_value(detail_state.get("wins"))
        losses = _counter_value(detail_state.get("losses"))
        draws = _counter_value(detail_state.get("draws"))

        gradients_obj = detail_state.get("gradients")
        grads = dict(gradients_obj) if isinstance(gradients_obj, Mapping) else None
        deltas_obj = detail_state.get("deltas")
        deltas = dict(deltas_obj) if isinstance(deltas_obj, Mapping) else None
        s_plus = coerce_float(detail_state.get("s_plus"))
        s_minus = coerce_float(detail_state.get("s_minus"))
        step = coerce_float(detail_state.get("step"))
        perturbations_obj = detail_state.get("perturbations")
        perturbations: dict[str, dict[str, float]] | None = None
        if isinstance(perturbations_obj, Mapping):
            normalized_perturbations: dict[str, dict[str, float]] = {}
            for phase_key, phase_values in perturbations_obj.items():
                if not isinstance(phase_values, Mapping):
                    continue
                values_map: dict[str, float] = {}
                for param_name, raw_value in phase_values.items():
                    value = coerce_float(json_serialize(raw_value))
                    if value is not None:
                        values_map[str(param_name)] = value
                normalized_perturbations[str(phase_key)] = values_map
            perturbations = normalized_perturbations
        gain_c_k = coerce_float(detail_state.get("c_k"))
        gain_a_k = coerce_float(detail_state.get("a_k"))
        is_pending = coerce_bool(detail_state.get("is_pending"))

        phase_wdl_raw = _parse_phase_wdl_payload(detail_state.get("phase_wdl"))
        phase_wdl: dict[str, WdlCounts] = {}
        for key, value in phase_wdl_raw.items():
            value_obj = {str(inner_key): json_serialize(inner_value) for inner_key, inner_value in value.items()}
            phase_wdl[key] = WdlCounts(
                wins=_counter_value(value_obj.get("wins")),
                losses=_counter_value(value_obj.get("losses")),
                draws=_counter_value(value_obj.get("draws")),
            )
        if not phase_wdl:
            phase_wdl = {
                "plus": WdlCounts(wins=0, losses=0, draws=0),
                "minus": WdlCounts(wins=0, losses=0, draws=0),
            }

        ltc_regression = parse_ltc_regression_detail(detail_state.get("ltc_regression"))

        def _collect_game_ids(raw_value: JsonValue | None) -> list[str]:
            if not isinstance(raw_value, list):
                return []
            return [item for item in raw_value if isinstance(item, str) and item.strip()]

        game_ids = _collect_game_ids(detail_state.get("game_ids"))
        ltc_game_ids = _collect_game_ids(detail_state.get("ltc_game_ids"))

        def _collect_game_order(raw_value: JsonValue | None) -> list[str]:
            if not isinstance(raw_value, list):
                return []
            return [item for item in raw_value if isinstance(item, str)]

        games_order = _collect_game_order(detail_state.get("games_order"))
        ltc_games_order = _collect_game_order(detail_state.get("ltc_games_order"))

        def _collect_game_meta(raw_value: JsonValue | None) -> dict[str, JsonObject]:
            validated_meta = _parse_games_meta_payload(raw_value)
            payload_map: dict[str, JsonObject] = {}
            for key, value in validated_meta.items():
                payload_map[key] = {str(inner_key): json_serialize(inner_val) for inner_key, inner_val in value.items()}
            return payload_map

        games_meta = _collect_game_meta(detail_state.get("games_meta"))
        ltc_games_meta = _collect_game_meta(detail_state.get("ltc_games_meta"))

        def register_game_record(game_id: str) -> JsonObject:
            entry = games_meta.get(game_id)
            if entry is None:
                entry = {"game_id": game_id}
                games_meta[game_id] = entry
                games_order.append(game_id)
            return entry

        def register_ltc_game_record(game_id: str) -> JsonObject:
            entry = ltc_games_meta.get(game_id)
            if entry is None:
                entry = {"game_id": game_id}
                ltc_games_meta[game_id] = entry
                ltc_games_order.append(game_id)
            return entry

        base_name, tuned_name = self._store.read_spsa_engine_names()

        if variant_id is None:
            variant_id = format_variant_label(idx)

        def normalize_variant_token(value: str | None) -> str | None:
            if not value:
                return None
            token = value.strip()
            if len(token) >= 2 and token[0].lower() == "v" and token[1:].isdigit():
                return token.lower()
            return None

        variant_token = normalize_variant_token(variant_id) or resolve_variant_id(idx)
        ltc_variant_token = None
        if ltc_regression is not None:
            tuned_token = ltc_regression.get("tuned_variant_token")
            if isinstance(tuned_token, str) and tuned_token.strip():
                ltc_variant_token = tuned_token.strip()
        ltc_patterns: list[str] = []
        primary_pattern = f"{ltc_variant_token or variant_token}-ltc-%"
        if primary_pattern:
            ltc_patterns.append(primary_pattern)

        repository = open_dashboard_repository(self._db_path)

        def hydrate_ltc_records_from_shogidb(db: ShogiRepositoryPort, ids: list[str]) -> None:
            record_store = DBRecordStore(db)
            for gid in ids[:50]:
                game_record = record_store.load(game_name=gid)
                if game_record is None:
                    continue
                record = register_ltc_game_record(gid)
                black_player = game_record.metadata.black_player
                if isinstance(black_player, str):
                    record.setdefault("black_player", black_player)
                white_player = game_record.metadata.white_player
                if isinstance(white_player, str):
                    record.setdefault("white_player", white_player)
                result = game_record.result
                game_result = result.name if result is not None else None
                if game_result is not None:
                    record.setdefault("game_result", game_result)
                record.setdefault("num_moves", len(game_record.moves))
                meta_snapshot = self._load_game_event_snapshot(gid)
                phase_value = meta_snapshot.get("phase") if isinstance(meta_snapshot, dict) else None
                update_idx_value = meta_snapshot.get("update_idx") if isinstance(meta_snapshot, dict) else None
                resolved_variant = resolve_variant_id(update_idx_value)
                if update_idx_value is None:
                    resolved_variant = extract_variant_from_game_id(gid) or resolved_variant
                if resolved_variant:
                    record.setdefault("variant_id", resolved_variant)
                if isinstance(phase_value, str):
                    record.setdefault("phase", phase_value)
                record.setdefault("status", "completed")
                start_time = game_record.metadata.start_date
                if isinstance(start_time, str) and start_time.strip():
                    record.setdefault("start_time", start_time)
                end_time = game_record.metadata.end_date
                if isinstance(end_time, str) and end_time.strip():
                    record.setdefault("end_time", end_time)

        if repository is not None:
            try:
                if game_ids:
                    record_store = DBRecordStore(repository)
                    for gid in game_ids[:50]:
                        game_record = record_store.load(game_name=gid)
                        if game_record is None:
                            continue
                        record = register_game_record(gid)
                        record["black_player"] = game_record.metadata.black_player
                        record["white_player"] = game_record.metadata.white_player
                        result = game_record.result
                        record["game_result"] = result.name if result is not None else None
                        record["num_moves"] = len(game_record.moves)
                        meta_snapshot = self._load_game_event_snapshot(gid)
                        phase_value = meta_snapshot.get("phase") if isinstance(meta_snapshot, dict) else None
                        update_idx_value = meta_snapshot.get("update_idx") if isinstance(meta_snapshot, dict) else None
                        resolved_variant = resolve_variant_id(update_idx_value)
                        if update_idx_value is None:
                            resolved_variant = extract_variant_from_game_id(gid) or resolved_variant
                        record["variant_id"] = resolved_variant or record.get("variant_id") or variant_token
                        if isinstance(phase_value, str):
                            record["phase"] = phase_value
                        record["status"] = record.get("status") or "completed"
                        start_time = game_record.metadata.start_date
                        if isinstance(start_time, str) and start_time.strip():
                            record["start_time"] = start_time
                        end_time = game_record.metadata.end_date
                        if isinstance(end_time, str) and end_time.strip():
                            record["end_time"] = end_time

                if not ltc_game_ids:
                    try:
                        session = repository.session
                        session.rollback()
                        for pattern in ltc_patterns:
                            query = select(Game.game_name).where(Game.game_type == "spsa", Game.game_name.like(pattern))
                            ltc_game_ids = [row[0] for row in session.execute(query).fetchall()]
                            if ltc_game_ids:
                                break
                    except SQLAlchemyError as exc:
                        logger.warning("Failed to load LTC games from DB (update=%s): %s", idx, exc)
                        ltc_game_ids = []
                if ltc_game_ids:
                    hydrate_ltc_records_from_shogidb(repository, ltc_game_ids)
            finally:
                repository.close_db()

        games_brief: list[GameBriefEntry] = []
        for gid in games_order:
            record_entry = games_meta.get(gid)
            if record_entry is None:
                continue
            variant_value = _as_optional_str(record_entry.get("variant_id"))
            games_brief.append(
                GameBriefEntry(
                    game_id=gid,
                    black_player=_as_optional_str(record_entry.get("black_player")),
                    white_player=_as_optional_str(record_entry.get("white_player")),
                    game_result=_as_optional_str(record_entry.get("game_result")),
                    num_moves=_as_optional_int(record_entry.get("num_moves")),
                    variant_id=variant_value or variant_token,
                    phase=_as_optional_str(record_entry.get("phase")),
                    status=_as_optional_str(record_entry.get("status")),
                    assigned_instance=_as_optional_str(record_entry.get("assigned_instance")),
                    round=_as_optional_int(record_entry.get("round")),
                    start_time=_as_optional_str(record_entry.get("start_time")),
                    end_time=_as_optional_str(record_entry.get("end_time")),
                )
            )

        ltc_games_brief: list[GameBriefEntry] = []
        for gid in ltc_games_order:
            record_entry = ltc_games_meta.get(gid)
            if record_entry is None:
                continue
            ltc_games_brief.append(
                GameBriefEntry(
                    game_id=gid,
                    black_player=_as_optional_str(record_entry.get("black_player")),
                    white_player=_as_optional_str(record_entry.get("white_player")),
                    game_result=_as_optional_str(record_entry.get("game_result")),
                    num_moves=_as_optional_int(record_entry.get("num_moves")),
                    variant_id=_as_optional_str(record_entry.get("variant_id")),
                    phase=_as_optional_str(record_entry.get("phase")),
                    status=_as_optional_str(record_entry.get("status")),
                    assigned_instance=_as_optional_str(record_entry.get("assigned_instance")),
                    round=_as_optional_int(record_entry.get("round")),
                    start_time=_as_optional_str(record_entry.get("start_time")),
                    end_time=_as_optional_str(record_entry.get("end_time")),
                )
            )

        wdl = WdlCounts(wins=wins, losses=losses, draws=draws)
        response_data: UpdateDetailResponse = {
            "update_idx": idx,
            "engines": {"baseline": base_name, "tuned": tuned_name},
            "wdl": wdl,
            "variant_id": variant_id,
            "params": params,
            "gradients": grads,
            "deltas": deltas,
            "s_plus": s_plus,
            "s_minus": s_minus,
            "step": step,
            "perturbations": perturbations,
            "c_k": gain_c_k,
            "a_k": gain_a_k,
            "is_pending": is_pending,
            "games": games_brief,
            "games_count": len(games_brief),
            "ltc_games": ltc_games_brief,
            "ltc_games_count": len(ltc_games_brief),
            "phase_wdl": phase_wdl,
            "ltc_regression": ltc_regression,
            "has_ltc_regression": coerce_bool(ltc_regression),
        }
        payload_data = {
            "update_idx": response_data["update_idx"],
            "engines": response_data["engines"],
            "wdl": response_data["wdl"],
            "variant_id": response_data["variant_id"],
            "params": response_data["params"],
            "gradients": response_data["gradients"],
            "deltas": response_data["deltas"],
            "s_plus": response_data["s_plus"],
            "s_minus": response_data["s_minus"],
            "step": response_data["step"],
            "perturbations": response_data["perturbations"],
            "c_k": response_data["c_k"],
            "a_k": response_data["a_k"],
            "is_pending": response_data["is_pending"],
            "games_count": response_data["games_count"],
            "ltc_games": response_data["ltc_games"],
            "ltc_games_count": response_data["ltc_games_count"],
            "phase_wdl": response_data["phase_wdl"],
            "ltc_regression": response_data["ltc_regression"],
            "has_ltc_regression": response_data["has_ltc_regression"],
        }
        response_data["payload"] = to_json_object(payload_data)
        return response_data
