"""SPSA LTC (Long Time Control) regression service."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from shogiarena.utils.types.coerce import coerce_float

from .bradley_terry import (
    ELO_SCALE,
    BradleyTerryEstimate,
    BradleyTerryMatch,
    estimate_bradley_terry,
    theta_to_elo,
)
from .store import SpsaStore
from .types import LtcBestEstimate, LtcSummary


def _coerce_int_round(value: Any) -> int | None:
    """``coerce_float`` 経由で ``round`` し ``int`` に変換する。

    ``_resolve_int`` の丸め（truncation ではなく四捨五入）を再現する。
    """
    f = coerce_float(value)
    if f is None:
        return None
    return int(round(f))


class SpsaLtcService:
    """Service for LTC regression summary and results."""

    def __init__(
        self,
        store: SpsaStore,
    ) -> None:
        self._store = store

    def compute_ltc_summary(
        self,
        *,
        enriched_results: list[dict[str, Any]] | None = None,
    ) -> LtcSummary:
        """Compute LTC regression summary from config, results, and index metadata."""
        ltc_config = self._store.load_meta_data().ltc_regression
        config_meta = ltc_config.model_dump(exclude_none=True) if ltc_config is not None else None

        if enriched_results is None:
            raw_results = self._store.load_ltc_results()
            enriched_results = self._enrich_ltc_results(raw_results)
        latest = enriched_results[-1] if enriched_results else None

        ltc_index = self._store.load_index_metadata().ltc_regression
        index_meta_dict: dict[str, Any] = ltc_index.model_dump(exclude_none=True) if ltc_index is not None else {}

        enabled = bool(config_meta.get("enabled")) if config_meta else bool(index_meta_dict)

        status = index_meta_dict.get("status") if index_meta_dict else None
        if status is None and latest:
            status = latest.get("status")
        if status is None:
            status = "pending" if enabled else "disabled"

        last_update_idx = index_meta_dict.get("last_update_idx") if index_meta_dict else None
        if last_update_idx is None and latest:
            update_idx_val = latest.get("update_idx")
            if isinstance(update_idx_val, int):
                last_update_idx = update_idx_val

        winrate = index_meta_dict.get("winrate") if index_meta_dict else None
        if winrate is None and latest is not None:
            winrate = latest.get("winrate")

        elo = index_meta_dict.get("elo") if index_meta_dict else None
        best_estimate: LtcBestEstimate | None = None
        if latest is not None:
            elo = latest.get("best_elo_mean", elo)
            best_estimate = LtcBestEstimate(
                mean=latest.get("best_elo_mean"),
                variance=latest.get("best_elo_variance"),
                sigma=latest.get("best_elo_sigma"),
                lower=latest.get("best_elo_lower_1sigma"),
                upper=latest.get("best_elo_upper_1sigma"),
                source=latest.get("best_estimate_source"),
                composition_depth=latest.get("best_composition_depth"),
                prob_positive=latest.get("best_positive_probability"),
            )

        pairs_played = index_meta_dict.get("pairs_played") if index_meta_dict else None
        if pairs_played is None and latest is not None:
            pairs_played = latest.get("pairs_played")

        sprt_meta = index_meta_dict.get("sprt") if index_meta_dict else None
        sprt = sprt_meta if isinstance(sprt_meta, dict) else None
        if sprt is None and latest is not None:
            sprt_latest = latest.get("sprt")
            sprt = sprt_latest if isinstance(sprt_latest, dict) else None

        sprt_decision = index_meta_dict.get("sprt_decision") if index_meta_dict else None
        if sprt_decision is None and sprt is not None:
            decision_val = sprt.get("decision")
            if isinstance(decision_val, str):
                sprt_decision = decision_val

        summary = LtcSummary(
            enabled=enabled,
            status=status,
            config=config_meta,
            last_update_idx=last_update_idx,
            winrate=winrate,
            elo=elo,
            pairs_played=pairs_played,
            sprt=sprt,
            sprt_decision=sprt_decision,
            latest=latest,
            history_size=len(enriched_results),
            best_estimate=best_estimate,
        )
        return summary

    def enrich_ltc_results(self, results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return LTC results enriched with cumulative best estimates."""
        return self._enrich_ltc_results(results)

    def _enrich_ltc_results(self, results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not results:
            return []

        baseline_anchor = -1
        enriched: list[dict[str, Any]] = []
        matches_prefix: list[BradleyTerryMatch] = []

        best_mean_map: dict[int, float] = {baseline_anchor: 0.0}
        best_variance_map: dict[int, float | None] = {baseline_anchor: 0.0}
        depth_map: dict[int, int] = {baseline_anchor: 0}
        source_map: dict[int, str] = {baseline_anchor: "baseline"}
        current_best_idx = baseline_anchor
        current_best_mean: float | None = 0.0
        current_best_variance: float | None = 0.0
        current_best_depth = 0
        current_best_source = "baseline"

        def summarise_best(estimate: BradleyTerryEstimate, *, has_data: bool) -> dict[str, Any]:
            theta_map = dict(estimate.theta)
            theta_map.setdefault(baseline_anchor, 0.0)

            best_variant = baseline_anchor
            best_theta = theta_map.get(baseline_anchor, 0.0)
            for variant, theta in theta_map.items():
                if variant == baseline_anchor:
                    continue
                if best_variant == baseline_anchor or theta > best_theta:
                    best_variant = variant
                    best_theta = theta

            variance_theta = estimate.variances.get(best_variant)
            if variance_theta is not None and variance_theta < 0:
                variance_theta = None

            mean_elo = theta_to_elo(best_theta)
            variance_elo: float | None = None
            sigma_elo: float | None = None
            lower: float | None = None
            upper: float | None = None
            prob_positive: float | None = None

            if variance_theta is not None:
                variance_elo = variance_theta * (ELO_SCALE**2)
                if variance_elo >= 0:
                    sigma_elo = math.sqrt(variance_elo)
                    lower = mean_elo - sigma_elo
                    upper = mean_elo + sigma_elo
                    if sigma_elo > 0:
                        prob_positive = 0.5 * (1.0 + math.erf(mean_elo / (math.sqrt(2.0) * sigma_elo)))
                    else:
                        if mean_elo > 0:
                            prob_positive = 1.0
                        elif mean_elo < 0:
                            prob_positive = 0.0
                        else:
                            prob_positive = 0.5
                else:
                    variance_elo = None

            source = "baseline" if best_variant == baseline_anchor and not has_data else "bradley-terry"
            return {
                "variant": best_variant,
                "theta": best_theta,
                "mean": mean_elo,
                "variance": variance_elo,
                "sigma": sigma_elo,
                "lower": lower,
                "upper": upper,
                "source": source,
                "prob_positive": prob_positive,
            }

        for raw_entry in results:
            entry = dict(raw_entry)

            update_idx = _coerce_int_round(entry.get("update_idx"))
            ordinal = update_idx
            baseline_idx = _coerce_int_round(entry.get("baseline_update_idx"))
            if baseline_idx is None:
                baseline_idx = current_best_idx
            if baseline_idx is None:
                baseline_idx = baseline_anchor

            status_raw = entry.get("status")
            status = status_raw.lower() if isinstance(status_raw, str) else "unknown"

            sprt = entry.get("sprt")
            sprt_dict = sprt if isinstance(sprt, dict) else {}

            baseline_mean = best_mean_map.get(baseline_idx, current_best_mean)
            baseline_variance = best_variance_map.get(baseline_idx, current_best_variance)
            baseline_depth = depth_map.get(baseline_idx, current_best_depth)
            baseline_source = source_map.get(
                baseline_idx,
                "baseline" if baseline_idx == baseline_anchor else "composed",
            )

            if baseline_mean is None and current_best_mean is not None:
                baseline_mean = current_best_mean
            if baseline_variance is None and current_best_variance is not None:
                baseline_variance = current_best_variance
            if baseline_depth is None:
                baseline_depth = current_best_depth
            if baseline_source is None:
                baseline_source = current_best_source

            if baseline_idx in best_mean_map:
                current_best_idx = baseline_idx
                current_best_mean = baseline_mean
                current_best_variance = baseline_variance
                current_best_depth = baseline_depth
                current_best_source = baseline_source

            tuned_wins = _coerce_int_round(entry.get("tuned_wins"))
            baseline_wins = _coerce_int_round(entry.get("baseline_wins"))
            draws = _coerce_int_round(entry.get("draws"))
            if tuned_wins is None and sprt_dict:
                tuned_wins = _coerce_int_round(sprt_dict.get("wins"))
            if baseline_wins is None and sprt_dict:
                baseline_wins = _coerce_int_round(sprt_dict.get("losses"))
            if draws is None and sprt_dict:
                draws = _coerce_int_round(sprt_dict.get("draws"))

            total_games = _coerce_int_round(entry.get("total_games"))
            if total_games is None and sprt_dict:
                total_games = _coerce_int_round(sprt_dict.get("games"))
            if total_games is None and sprt_dict:
                total_games = _coerce_int_round(sprt_dict.get("games_played"))
            if total_games is None and tuned_wins is not None and baseline_wins is not None:
                draws_val = draws if draws is not None else 0
                total_games = tuned_wins + baseline_wins + draws_val

            effective_games = None
            if tuned_wins is not None and baseline_wins is not None:
                effective_games = tuned_wins + baseline_wins

            winrate = coerce_float(entry.get("winrate"))
            if winrate is None and sprt_dict:
                winrate = coerce_float(sprt_dict.get("winrate"))
            if winrate is None and tuned_wins is not None and total_games is not None and total_games > 0:
                draws_val = draws if draws is not None else 0
                winrate = (tuned_wins + draws_val * 0.5) / total_games

            delta_mean = coerce_float(entry.get("elo"))
            if delta_mean is None and sprt_dict:
                delta_mean = coerce_float(sprt_dict.get("elo"))
            if delta_mean is None:
                delta_mean = self._elo_from_winrate(winrate)

            delta_variance = self._variance_from_counts(tuned_wins, baseline_wins)

            prior_estimate = estimate_bradley_terry(matches_prefix, baseline_id=baseline_anchor)
            prior_summary = summarise_best(prior_estimate, has_data=bool(matches_prefix))
            align_with_best = baseline_idx == prior_summary["variant"]

            match_draws = draws if draws is not None else 0
            if status == "passed" and (
                update_idx is not None
                and tuned_wins is not None
                and baseline_wins is not None
                and (tuned_wins + baseline_wins + match_draws) > 0
            ):
                matches_prefix.append(
                    BradleyTerryMatch(
                        player_a=baseline_idx,
                        player_b=update_idx,
                        wins_a=float(baseline_wins),
                        wins_b=float(tuned_wins),
                        draws=float(match_draws),
                    )
                )

            post_estimate = estimate_bradley_terry(matches_prefix, baseline_id=baseline_anchor)
            post_summary = summarise_best(post_estimate, has_data=bool(matches_prefix))

            updated_best_idx = current_best_idx
            updated_best_mean = current_best_mean
            updated_best_variance = current_best_variance
            updated_best_depth = current_best_depth
            updated_best_source = current_best_source

            effective_delta_mean = delta_mean
            if effective_delta_mean is None and post_summary["mean"] is not None:
                base_mean_for_delta = baseline_mean if baseline_mean is not None else 0.0
                effective_delta_mean = post_summary["mean"] - base_mean_for_delta

            effective_delta_variance = delta_variance
            if effective_delta_variance is None:
                effective_delta_variance = post_summary["variance"]

            if baseline_mean is None:
                baseline_mean = 0.0

            if status == "passed":
                new_mean = baseline_mean + (effective_delta_mean or 0.0)
                if effective_delta_variance is None:
                    new_variance = baseline_variance
                elif baseline_variance is None:
                    new_variance = effective_delta_variance
                else:
                    new_variance = baseline_variance + effective_delta_variance
                new_depth = (baseline_depth or 0) + 1
                new_source = "direct" if baseline_idx == baseline_anchor else "composed"

                if update_idx is not None:
                    best_mean_map[update_idx] = new_mean
                    best_variance_map[update_idx] = new_variance
                    depth_map[update_idx] = new_depth
                    source_map[update_idx] = new_source
                    updated_best_idx = update_idx

                updated_best_mean = new_mean
                updated_best_variance = new_variance
                updated_best_depth = new_depth
                updated_best_source = new_source

            current_best_idx = updated_best_idx
            current_best_mean = updated_best_mean
            current_best_variance = updated_best_variance
            current_best_depth = updated_best_depth
            current_best_source = updated_best_source

            def _prob_from_stats(mean: float | None, variance: float | None) -> float | None:
                if mean is None or variance is None or variance < 0:
                    return None
                if variance == 0:
                    if mean > 0:
                        return 1.0
                    if mean < 0:
                        return 0.0
                    return 0.5
                sigma_val = math.sqrt(variance)
                return 0.5 * (1.0 + math.erf(mean / (math.sqrt(2.0) * sigma_val)))

            sprt_decision_raw = entry.get("sprt_decision")
            sprt_decision = sprt_decision_raw if isinstance(sprt_decision_raw, str) else None
            if not sprt_decision and sprt_dict:
                decision_value = sprt_dict.get("decision")
                if isinstance(decision_value, str):
                    sprt_decision = decision_value

            sprt_elo = coerce_float(sprt_dict.get("elo")) if sprt_dict else None
            sprt_games = None
            if sprt_dict:
                sprt_games = _coerce_int_round(sprt_dict.get("games"))
                if sprt_games is None:
                    sprt_games = _coerce_int_round(sprt_dict.get("games_played"))

            sprt_anchor_mean = prior_summary["mean"]
            sprt_point_value = None
            if sprt_anchor_mean is not None and sprt_elo is not None:
                sprt_point_value = sprt_anchor_mean + sprt_elo

            best_prior_sigma = (
                math.sqrt(baseline_variance) if baseline_variance is not None and baseline_variance >= 0 else None
            )

            best_sigma = (
                math.sqrt(updated_best_variance)
                if updated_best_variance is not None and updated_best_variance >= 0
                else None
            )
            best_lower = None
            best_upper = None
            if best_sigma is not None and updated_best_mean is not None:
                best_lower = updated_best_mean - best_sigma
                best_upper = updated_best_mean + best_sigma

            prior_prob = prior_summary.get("prob_positive")
            if prior_prob is None:
                prior_prob = _prob_from_stats(baseline_mean, baseline_variance)

            post_prob = post_summary.get("prob_positive")
            if post_prob is None:
                post_prob = _prob_from_stats(updated_best_mean, updated_best_variance)

            entry.update(
                {
                    "ordinal": ordinal,
                    "baseline_update_idx": baseline_idx,
                    "variant_idx": update_idx,
                    "status": status,
                    "tuned_wins": tuned_wins,
                    "baseline_wins": baseline_wins,
                    "draws": draws,
                    "total_games": total_games,
                    "delta_elo_mean": delta_mean,
                    "delta_elo_variance": delta_variance,
                    "delta_effective_games": effective_games,
                    "best_elo_mean_prior": baseline_mean,
                    "best_elo_variance_prior": baseline_variance,
                    "best_elo_sigma_prior": best_prior_sigma,
                    "best_elo_mean": updated_best_mean,
                    "best_elo_variance": updated_best_variance,
                    "best_elo_sigma": best_sigma,
                    "best_elo_lower_1sigma": best_lower,
                    "best_elo_upper_1sigma": best_upper,
                    "best_estimate_source": updated_best_source,
                    "best_positive_probability_prior": prior_prob,
                    "best_positive_probability": post_prob,
                    "best_composition_depth": updated_best_depth,
                    "best_update_idx": updated_best_idx,
                    "sprt_result": sprt_decision,
                    "sprt_elo": sprt_elo,
                    "sprt_games": sprt_games,
                    "sprt_llr": coerce_float(sprt_dict.get("llr")) if sprt_dict else None,
                    "sprt_lower_bound": coerce_float(sprt_dict.get("lower")) if sprt_dict else None,
                    "sprt_upper_bound": coerce_float(sprt_dict.get("upper")) if sprt_dict else None,
                    "sprt_winrate": coerce_float(sprt_dict.get("winrate")) if sprt_dict else None,
                    "sprt_anchor_mean": sprt_anchor_mean,
                    "sprt_point_value": sprt_point_value,
                    "direct_vs_initial": baseline_idx == baseline_anchor,
                    "composition_aligned_with_best": align_with_best,
                }
            )

            enriched.append(entry)

        return enriched

    @staticmethod
    def _elo_from_winrate(winrate: float | None) -> float | None:
        if winrate is None:
            return None
        clamped = max(0.0005, min(0.9995, winrate))
        try:
            return -400.0 * math.log10(1.0 / clamped - 1.0)
        except (ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _variance_from_counts(wins: int | None, losses: int | None) -> float | None:
        if wins is None or losses is None:
            return None
        games = wins + losses
        if games <= 0:
            return None
        p = wins / games if games else 0.5
        p = max(0.0005, min(0.9995, p))
        try:
            coefficient = (400.0 / math.log(10.0)) ** 2
        except (ValueError, ZeroDivisionError):
            return None
        denom = games * p * (1.0 - p)
        if denom <= 0:
            return None
        return coefficient / denom
