"""Entry parsing helpers for dashboard LTC regression enrichment."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float


def round_optional_int(value: JsonValue | None) -> int | None:
    """Round ``value`` via ``coerce_float`` and return ``int``."""

    coerced = coerce_float(value)
    if coerced is None:
        return None
    return int(round(coerced))


@dataclass(slots=True)
class _LtcEntryData:
    entry: JsonObject
    update_idx: int | None
    ordinal: int | None
    baseline_idx: int | None
    status: str
    sprt_dict: JsonObject
    tuned_wins: int | None
    baseline_wins: int | None
    draws: int | None
    total_games: int | None
    effective_games: int | None
    delta_mean: float | None
    delta_variance: float | None


@dataclass(slots=True)
class _SprtDetails:
    decision: str | None
    elo: float | None
    games: int | None
    llr: float | None
    lower: float | None
    upper: float | None
    winrate: float | None


def parse_ltc_entry(raw_entry: Mapping[str, JsonValue]) -> _LtcEntryData:
    """Normalise one raw LTC event entry into structured data."""
    entry = to_json_object(raw_entry)
    update_idx = round_optional_int(entry.get("update_idx"))
    baseline_idx = round_optional_int(entry.get("baseline_update_idx"))
    status_raw = entry.get("status")
    status = status_raw.lower() if isinstance(status_raw, str) else "unknown"
    sprt = entry.get("sprt")
    sprt_dict: JsonObject = to_json_object(sprt) if is_str_object_mapping(sprt) else {}
    tuned_wins, baseline_wins, draws = _extract_match_counts(entry, sprt_dict)
    total_games = _extract_total_games(entry, sprt_dict, tuned_wins, baseline_wins, draws)
    effective_games = tuned_wins + baseline_wins if tuned_wins is not None and baseline_wins is not None else None
    winrate = _extract_winrate(entry, sprt_dict, tuned_wins, draws, total_games)
    delta_mean = _extract_delta_mean(entry, sprt_dict, winrate)
    delta_variance = _variance_from_counts(tuned_wins, baseline_wins)
    return _LtcEntryData(
        entry=entry,
        update_idx=update_idx,
        ordinal=update_idx,
        baseline_idx=baseline_idx,
        status=status,
        sprt_dict=sprt_dict,
        tuned_wins=tuned_wins,
        baseline_wins=baseline_wins,
        draws=draws,
        total_games=total_games,
        effective_games=effective_games,
        delta_mean=delta_mean,
        delta_variance=delta_variance,
    )


def extract_sprt_details(data: _LtcEntryData) -> _SprtDetails:
    """Extract SPRT-side metrics from one parsed LTC entry."""
    sprt_decision_raw = data.entry.get("sprt_decision")
    sprt_decision = sprt_decision_raw if isinstance(sprt_decision_raw, str) else None
    if not sprt_decision and data.sprt_dict:
        decision_value = data.sprt_dict.get("decision")
        if isinstance(decision_value, str):
            sprt_decision = decision_value
    sprt_games = round_optional_int(data.sprt_dict.get("games")) if data.sprt_dict else None
    if sprt_games is None and data.sprt_dict:
        sprt_games = round_optional_int(data.sprt_dict.get("games_played"))
    return _SprtDetails(
        decision=sprt_decision,
        elo=coerce_float(data.sprt_dict.get("elo")) if data.sprt_dict else None,
        games=sprt_games,
        llr=coerce_float(data.sprt_dict.get("llr")) if data.sprt_dict else None,
        lower=coerce_float(data.sprt_dict.get("lower")) if data.sprt_dict else None,
        upper=coerce_float(data.sprt_dict.get("upper")) if data.sprt_dict else None,
        winrate=coerce_float(data.sprt_dict.get("winrate")) if data.sprt_dict else None,
    )


def _extract_match_counts(entry: JsonObject, sprt_dict: JsonObject) -> tuple[int | None, int | None, int | None]:
    tuned_wins = round_optional_int(entry.get("tuned_wins"))
    baseline_wins = round_optional_int(entry.get("baseline_wins"))
    draws = round_optional_int(entry.get("draws"))
    if tuned_wins is None and sprt_dict:
        tuned_wins = round_optional_int(sprt_dict.get("wins"))
    if baseline_wins is None and sprt_dict:
        baseline_wins = round_optional_int(sprt_dict.get("losses"))
    if draws is None and sprt_dict:
        draws = round_optional_int(sprt_dict.get("draws"))
    return tuned_wins, baseline_wins, draws


def _extract_total_games(
    entry: JsonObject,
    sprt_dict: JsonObject,
    tuned_wins: int | None,
    baseline_wins: int | None,
    draws: int | None,
) -> int | None:
    total_games = round_optional_int(entry.get("total_games"))
    if total_games is None and sprt_dict:
        total_games = round_optional_int(sprt_dict.get("games"))
    if total_games is None and sprt_dict:
        total_games = round_optional_int(sprt_dict.get("games_played"))
    if total_games is None and tuned_wins is not None and baseline_wins is not None:
        total_games = tuned_wins + baseline_wins + (draws if draws is not None else 0)
    return total_games


def _extract_winrate(
    entry: JsonObject,
    sprt_dict: JsonObject,
    tuned_wins: int | None,
    draws: int | None,
    total_games: int | None,
) -> float | None:
    winrate = coerce_float(entry.get("winrate"))
    if winrate is None and sprt_dict:
        winrate = coerce_float(sprt_dict.get("winrate"))
    if winrate is None and tuned_wins is not None and total_games is not None and total_games > 0:
        winrate = (tuned_wins + (draws if draws is not None else 0) * 0.5) / total_games
    return winrate


def _extract_delta_mean(entry: JsonObject, sprt_dict: JsonObject, winrate: float | None) -> float | None:
    delta_mean = coerce_float(entry.get("elo"))
    if delta_mean is None and sprt_dict:
        delta_mean = coerce_float(sprt_dict.get("elo"))
    if delta_mean is None:
        delta_mean = _elo_from_winrate(winrate)
    return delta_mean


def _elo_from_winrate(winrate: float | None) -> float | None:
    if winrate is None:
        return None
    clamped = max(0.0005, min(0.9995, winrate))
    try:
        return -400.0 * math.log10(1.0 / clamped - 1.0)
    except (ValueError, ZeroDivisionError):
        return None


def _variance_from_counts(wins: int | None, losses: int | None) -> float | None:
    if wins is None or losses is None:
        return None
    games = wins + losses
    if games <= 0:
        return None
    probability = wins / games if games else 0.5
    probability = max(0.0005, min(0.9995, probability))
    try:
        coefficient = (400.0 / math.log(10.0)) ** 2
    except (ValueError, ZeroDivisionError):
        return None
    denominator = games * probability * (1.0 - probability)
    if denominator <= 0:
        return None
    return coefficient / denominator


__all__ = ["_LtcEntryData", "_SprtDetails", "extract_sprt_details", "parse_ltc_entry", "round_optional_int"]
