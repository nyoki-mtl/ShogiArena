from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeAlias, TypedDict, cast

from shogiarena.arena.configs.tournament import TournamentResults
from shogiarena.arena.results.base import RunResultBase
from shogiarena.arena.results.sprt import SprtRunResult
from shogiarena.arena.results.spsa import SpsaRunResult
from shogiarena.arena.results.tournament import TournamentRunResult
from shogiarena.arena.services.statistics.sprt import SprtDecision, SprtResult
from shogiarena.arena.storage import RunStorage
from shogiarena.utils.serialization import json_serialize
from shogiarena.utils.types.coerce import coerce_float, coerce_int

logger = logging.getLogger(__name__)


JsonMapping: TypeAlias = Mapping[str, object]


class _ResultStorePayload(TypedDict):
    """``save_result`` が永続化するペイロード。"""

    run_id: str
    type: str
    run_dir: str
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    summary: object
    config_snapshot: object
    result: dict[str, object]


class ResultStore(Protocol):
    """RunResult の保存/読み出しを抽象化するインターフェース。"""

    def save_result(self, result: RunResultBase) -> None: ...

    def load_result(self, run_id: str) -> RunResultBase | None: ...


class RunStorageResultStore:
    """RunStorage をバックエンドとする ResultStore 実装。"""

    def __init__(self, storage: RunStorage) -> None:
        self._storage = storage

    def save_result(self, result: RunResultBase) -> None:
        payload: _ResultStorePayload = {
            "run_id": result.run_id,
            "type": type(result).__name__,
            "run_dir": str(result.run_dir),
            "started_at": _format_datetime(result.started_at),
            "completed_at": _format_datetime(result.completed_at),
            "duration_ms": result.duration_ms(),
            "summary": json_serialize(result.summary),
            "config_snapshot": json_serialize(result.config_snapshot),
            "result": _serialize_result_extras(result),
        }
        self._storage.write_json(f"results/{result.run_id}.json", payload)

    def load_result(self, run_id: str) -> RunResultBase | None:
        payload = self._storage.read_json(f"results/{run_id}.json")
        if payload is None:
            return None
        return _deserialize_result_payload(payload, self._storage)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.warning("Invalid ISO datetime string: %r", value)
        return None


def _coerce_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return None


def _serialize_result_extras(result: RunResultBase) -> dict[str, object]:
    return result.serialize_extras()


def _deserialize_result_payload(payload: Mapping[str, object], storage: RunStorage) -> RunResultBase | None:
    result_type = payload.get("type")
    run_id = str(payload.get("run_id") or "")
    run_dir_raw = payload.get("run_dir")
    run_dir = Path(run_dir_raw) if isinstance(run_dir_raw, str) and run_dir_raw else storage.run_dir
    summary = _coerce_mapping(payload.get("summary"))
    config_snapshot = _coerce_mapping(payload.get("config_snapshot"))
    started_at = _parse_datetime(
        str(payload.get("started_at")) if isinstance(payload.get("started_at"), str | bytes) else None
    )
    completed_at = _parse_datetime(
        str(payload.get("completed_at")) if isinstance(payload.get("completed_at"), str | bytes) else None
    )
    extras_raw = payload.get("result")
    extras = cast(dict[str, object], extras_raw) if isinstance(extras_raw, dict) else {}

    if result_type == "TournamentRunResult":
        tournament_payload = extras.get("tournament")
        tournament = None
        if isinstance(tournament_payload, dict):
            tournament = TournamentResults(**cast(dict[str, Any], tournament_payload))
        sprt_payload = extras.get("sprt")
        sprt = _deserialize_sprt_result(sprt_payload)
        if tournament is None:
            return None
        return TournamentRunResult(
            tournament=tournament,
            sprt=sprt,
            run_id=run_id,
            run_dir=run_dir,
            storage=storage,
            summary=summary,
            config_snapshot=config_snapshot,
            started_at=started_at,
            completed_at=completed_at,
        )

    if result_type == "SpsaRunResult":
        final_params_raw = extras.get("final_params")
        final_params: Mapping[str, float] = {}
        if isinstance(final_params_raw, dict):
            validated: dict[str, float] = {}
            for k, v in final_params_raw.items():
                coerced = coerce_float(v)
                if coerced is not None:
                    validated[str(k)] = coerced
                else:
                    logger.warning("final_params[%s]: float に変換できません (%r)", k, v)
            final_params = validated
        return SpsaRunResult(
            final_params=final_params,
            run_id=run_id,
            run_dir=run_dir,
            storage=storage,
            summary=summary,
            config_snapshot=config_snapshot,
            started_at=started_at,
            completed_at=completed_at,
        )

    if result_type == "SprtRunResult":
        sprt_payload = extras.get("sprt")
        sprt = _deserialize_sprt_result(sprt_payload)
        if sprt is None:
            return None
        return SprtRunResult(
            sprt=sprt,
            run_id=run_id,
            run_dir=run_dir,
            storage=storage,
            summary=summary,
            config_snapshot=config_snapshot,
            started_at=started_at,
            completed_at=completed_at,
        )

    logger.warning("Unknown result type: %s", result_type)
    return None


def _get_float(d: dict[str, object], key: str, default: float = 0.0) -> float:
    """dict から float 値を安全に取得する。"""
    result = coerce_float(d.get(key))
    return result if result is not None else default


def _get_int(d: dict[str, object], key: str, default: int = 0) -> int:
    """dict から int 値を安全に取得する。"""
    result = coerce_int(d.get(key))
    return result if result is not None else default


def _deserialize_sprt_result(payload: object) -> SprtResult | None:
    if not isinstance(payload, dict):
        return None
    p = cast(dict[str, object], payload)
    decision_raw = p.get("decision")
    try:
        decision = SprtDecision(decision_raw) if decision_raw is not None else SprtDecision.CONTINUE
    except ValueError:
        decision = SprtDecision.CONTINUE
    elo_est = p.get("elo_estimate")
    return SprtResult(
        llr=_get_float(p, "llr"),
        lower_bound=_get_float(p, "lower_bound"),
        upper_bound=_get_float(p, "upper_bound"),
        decision=decision,
        games_played=_get_int(p, "games_played"),
        wins=_get_int(p, "wins"),
        draws=_get_int(p, "draws"),
        losses=_get_int(p, "losses"),
        win_rate=_get_float(p, "win_rate"),
        elo_estimate=coerce_float(elo_est),
    )
