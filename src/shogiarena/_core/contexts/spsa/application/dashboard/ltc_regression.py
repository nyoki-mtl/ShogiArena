"""Application-layer LTC regression analytics for SPSA dashboard."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .bradley_terry import (
    ELO_SCALE,
    BradleyTerryEstimate,
    BradleyTerryMatch,
    estimate_bradley_terry,
    theta_to_elo,
)
from .ltc_regression_entry_parsing import (
    _LtcEntryData,
    _SprtDetails,
    extract_sprt_details,
    parse_ltc_entry,
)


class _BestSummary(TypedDict):
    variant: int
    theta: float
    mean: float | None
    variance: float | None
    sigma: float | None
    lower: float | None
    upper: float | None
    source: str
    prob_positive: float | None


@dataclass(slots=True)
class _BestUpdate:
    idx: int
    mean: float | None
    variance: float | None
    depth: int
    source: str


@dataclass(slots=True)
class _LtcEnrichmentState:
    baseline_anchor: int = -1
    matches_prefix: list[BradleyTerryMatch] = field(default_factory=list)
    best_mean_map: dict[int, float] = field(default_factory=lambda: {-1: 0.0})
    best_variance_map: dict[int, float | None] = field(default_factory=lambda: {-1: 0.0})
    depth_map: dict[int, int] = field(default_factory=lambda: {-1: 0})
    source_map: dict[int, str] = field(default_factory=lambda: {-1: "baseline"})
    current_best_idx: int = -1
    current_best_mean: float | None = 0.0
    current_best_variance: float | None = 0.0
    current_best_depth: int = 0
    current_best_source: str = "baseline"

    def resolve_baseline(self, baseline_idx: int | None) -> tuple[int, float | None, float | None, int, str]:
        resolved_idx = self.current_best_idx if baseline_idx is None else baseline_idx
        baseline_mean = self.best_mean_map.get(resolved_idx, self.current_best_mean)
        baseline_variance = self.best_variance_map.get(resolved_idx, self.current_best_variance)
        baseline_depth = self.depth_map.get(resolved_idx, self.current_best_depth)
        baseline_source = self.source_map.get(
            resolved_idx,
            "baseline" if resolved_idx == self.baseline_anchor else "composed",
        )
        if baseline_mean is None and self.current_best_mean is not None:
            baseline_mean = self.current_best_mean
        if baseline_variance is None and self.current_best_variance is not None:
            baseline_variance = self.current_best_variance
        if resolved_idx in self.best_mean_map:
            self.set_current(
                idx=resolved_idx,
                mean=baseline_mean,
                variance=baseline_variance,
                depth=baseline_depth,
                source=baseline_source,
            )
        return resolved_idx, baseline_mean, baseline_variance, baseline_depth, baseline_source

    def set_current(
        self,
        *,
        idx: int,
        mean: float | None,
        variance: float | None,
        depth: int,
        source: str,
    ) -> None:
        self.current_best_idx = idx
        self.current_best_mean = mean
        self.current_best_variance = variance
        self.current_best_depth = depth
        self.current_best_source = source


def enrich_ltc_results(results: Sequence[Mapping[str, JsonValue]]) -> list[JsonObject]:
    """Return LTC results enriched with cumulative best estimates."""

    if not results:
        return []
    state = _LtcEnrichmentState()
    return [_enrich_ltc_entry(raw_entry, state=state) for raw_entry in results]


def _enrich_ltc_entry(raw_entry: Mapping[str, JsonValue], *, state: _LtcEnrichmentState) -> JsonObject:
    data = parse_ltc_entry(raw_entry)
    baseline_idx, baseline_mean, baseline_variance, baseline_depth, _ = state.resolve_baseline(data.baseline_idx)
    prior_summary = _summarise_matches(state, baseline_anchor=state.baseline_anchor)
    is_aligned_with_best = baseline_idx == prior_summary["variant"]
    _append_match_if_passed(data=data, baseline_idx=baseline_idx, state=state)
    post_summary = _summarise_matches(state, baseline_anchor=state.baseline_anchor)
    updated = _resolve_updated_best(
        data=data,
        state=state,
        baseline_idx=baseline_idx,
        baseline_mean=baseline_mean,
        baseline_variance=baseline_variance,
        baseline_depth=baseline_depth,
        post_summary=post_summary,
    )
    state.set_current(
        idx=updated.idx,
        mean=updated.mean,
        variance=updated.variance,
        depth=updated.depth,
        source=updated.source,
    )
    prior_prob = _resolve_probability(prior_summary.get("prob_positive"), baseline_mean, baseline_variance)
    post_prob = _resolve_probability(post_summary.get("prob_positive"), updated.mean, updated.variance)
    best_prior_sigma = _sigma_or_none(baseline_variance)
    best_sigma, best_lower, best_upper = _sigma_band(updated.mean, updated.variance)
    sprt = extract_sprt_details(data)
    sprt_anchor_mean = prior_summary["mean"]
    sprt_point_value = sprt_anchor_mean + sprt.elo if sprt_anchor_mean is not None and sprt.elo is not None else None
    data.entry.update(
        _build_enriched_fields(
            data=data,
            baseline_idx=baseline_idx,
            baseline_mean=baseline_mean,
            baseline_variance=baseline_variance,
            best_prior_sigma=best_prior_sigma,
            updated=updated,
            best_sigma=best_sigma,
            best_lower=best_lower,
            best_upper=best_upper,
            prior_prob=prior_prob,
            post_prob=post_prob,
            sprt=sprt,
            sprt_anchor_mean=sprt_anchor_mean,
            sprt_point_value=sprt_point_value,
            baseline_anchor=state.baseline_anchor,
            is_aligned_with_best=is_aligned_with_best,
        )
    )
    return data.entry


def _append_match_if_passed(data: _LtcEntryData, *, baseline_idx: int, state: _LtcEnrichmentState) -> None:
    if data.status != "passed":
        return
    if data.update_idx is None or data.tuned_wins is None or data.baseline_wins is None:
        return
    match_draws = data.draws if data.draws is not None else 0
    if (data.tuned_wins + data.baseline_wins + match_draws) <= 0:
        return
    state.matches_prefix.append(
        BradleyTerryMatch(
            player_a=baseline_idx,
            player_b=data.update_idx,
            wins_a=float(data.baseline_wins),
            wins_b=float(data.tuned_wins),
            draws=float(match_draws),
        )
    )


def _summarise_matches(state: _LtcEnrichmentState, *, baseline_anchor: int) -> _BestSummary:
    estimate = estimate_bradley_terry(state.matches_prefix, baseline_id=baseline_anchor)
    return _summarise_best(estimate, baseline_anchor=baseline_anchor, has_data=bool(state.matches_prefix))


def _summarise_best(
    estimate: BradleyTerryEstimate,
    *,
    baseline_anchor: int,
    has_data: bool,
) -> _BestSummary:
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
    variance_elo = variance_theta * (ELO_SCALE**2) if variance_theta is not None else None
    sigma_elo, lower, upper = _sigma_band(mean_elo, variance_elo)
    prob_positive = _prob_from_stats(mean_elo, variance_elo)
    source = "baseline" if best_variant == baseline_anchor and not has_data else "bradley-terry"
    return {
        "variant": best_variant,
        "theta": best_theta,
        "mean": mean_elo,
        "variance": variance_elo if variance_elo is not None and variance_elo >= 0 else None,
        "sigma": sigma_elo,
        "lower": lower,
        "upper": upper,
        "source": source,
        "prob_positive": prob_positive,
    }


def _resolve_updated_best(
    *,
    data: _LtcEntryData,
    state: _LtcEnrichmentState,
    baseline_idx: int,
    baseline_mean: float | None,
    baseline_variance: float | None,
    baseline_depth: int,
    post_summary: _BestSummary,
) -> _BestUpdate:
    updated = _BestUpdate(
        idx=state.current_best_idx,
        mean=state.current_best_mean,
        variance=state.current_best_variance,
        depth=state.current_best_depth,
        source=state.current_best_source,
    )
    if data.status != "passed":
        return updated
    effective_delta_mean = data.delta_mean
    if effective_delta_mean is None and post_summary["mean"] is not None:
        base_mean_for_delta = baseline_mean if baseline_mean is not None else 0.0
        post_mean = post_summary["mean"]
        if post_mean is not None:
            effective_delta_mean = post_mean - base_mean_for_delta
    effective_delta_variance = data.delta_variance if data.delta_variance is not None else post_summary["variance"]
    baseline_mean_value = baseline_mean if baseline_mean is not None else 0.0
    new_mean = baseline_mean_value + (effective_delta_mean or 0.0)
    if effective_delta_variance is None:
        new_variance = baseline_variance
    elif baseline_variance is None:
        new_variance = effective_delta_variance
    else:
        new_variance = baseline_variance + effective_delta_variance
    new_depth = baseline_depth + 1
    new_source = "direct" if baseline_idx == state.baseline_anchor else "composed"
    if data.update_idx is not None:
        state.best_mean_map[data.update_idx] = new_mean
        state.best_variance_map[data.update_idx] = new_variance
        state.depth_map[data.update_idx] = new_depth
        state.source_map[data.update_idx] = new_source
        updated.idx = data.update_idx
    updated.mean = new_mean
    updated.variance = new_variance
    updated.depth = new_depth
    updated.source = new_source
    return updated


def _build_enriched_fields(
    *,
    data: _LtcEntryData,
    baseline_idx: int,
    baseline_mean: float | None,
    baseline_variance: float | None,
    best_prior_sigma: float | None,
    updated: _BestUpdate,
    best_sigma: float | None,
    best_lower: float | None,
    best_upper: float | None,
    prior_prob: float | None,
    post_prob: float | None,
    sprt: _SprtDetails,
    sprt_anchor_mean: float | None,
    sprt_point_value: float | None,
    baseline_anchor: int,
    is_aligned_with_best: bool,
) -> JsonObject:
    return {
        "ordinal": data.ordinal,
        "baseline_update_idx": baseline_idx,
        "variant_idx": data.update_idx,
        "status": data.status,
        "tuned_wins": data.tuned_wins,
        "baseline_wins": data.baseline_wins,
        "draws": data.draws,
        "total_games": data.total_games,
        "delta_elo_mean": data.delta_mean,
        "delta_elo_variance": data.delta_variance,
        "delta_effective_games": data.effective_games,
        "best_elo_mean_prior": baseline_mean,
        "best_elo_variance_prior": baseline_variance,
        "best_elo_sigma_prior": best_prior_sigma,
        "best_elo_mean": updated.mean,
        "best_elo_variance": updated.variance,
        "best_elo_sigma": best_sigma,
        "best_elo_lower_1sigma": best_lower,
        "best_elo_upper_1sigma": best_upper,
        "best_estimate_source": updated.source,
        "best_positive_probability_prior": prior_prob,
        "best_positive_probability": post_prob,
        "best_composition_depth": updated.depth,
        "best_update_idx": updated.idx,
        "sprt_result": sprt.decision,
        "sprt_elo": sprt.elo,
        "sprt_games": sprt.games,
        "sprt_llr": sprt.llr,
        "sprt_lower_bound": sprt.lower,
        "sprt_upper_bound": sprt.upper,
        "sprt_winrate": sprt.winrate,
        "sprt_anchor_mean": sprt_anchor_mean,
        "sprt_point_value": sprt_point_value,
        "direct_vs_initial": baseline_idx == baseline_anchor,
        "composition_aligned_with_best": is_aligned_with_best,
    }


def _resolve_probability(
    summary_probability: float | None,
    mean: float | None,
    variance: float | None,
) -> float | None:
    if summary_probability is not None:
        return summary_probability
    return _prob_from_stats(mean, variance)


def _sigma_or_none(variance: float | None) -> float | None:
    if variance is None or variance < 0:
        return None
    return math.sqrt(variance)


def _sigma_band(mean: float | None, variance: float | None) -> tuple[float | None, float | None, float | None]:
    sigma = _sigma_or_none(variance)
    if sigma is None or mean is None:
        return sigma, None, None
    return sigma, mean - sigma, mean + sigma


def _prob_from_stats(mean: float | None, variance: float | None) -> float | None:
    sigma = _sigma_or_none(variance)
    if mean is None or sigma is None:
        return None
    if sigma == 0:
        if mean > 0:
            return 1.0
        if mean < 0:
            return 0.0
        return 0.5
    return 0.5 * (1.0 + math.erf(mean / (math.sqrt(2.0) * sigma)))


__all__ = ["enrich_ltc_results"]
