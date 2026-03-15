"""Convergence analysis routines for SPSA dashboard update streams."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int

UpdatePayload = Mapping[str, JsonValue]


@dataclass(slots=True, frozen=True)
class ConvergenceMetricsResult:
    """Computed convergence metrics for SPSA update stream."""

    recent_avg_delta_norm: float | None = None
    overall_avg_delta_norm: float | None = None
    recent_std_delta_norm: float | None = None
    trend_slope: float | None = None
    is_converging: bool | None = None
    convergence_confidence: float | None = None
    recent_mean_vector_delta_norm: float | None = None
    recent_avg_mean_vector_delta_norm: float | None = None


@dataclass(slots=True, frozen=True)
class ConvergencePredictionResult:
    """Convergence prediction derived from recent trends."""

    remaining_updates_estimate: float | None = None
    convergence_probability: float | None = None


@dataclass(slots=True, frozen=True)
class ConvergenceAnalysisResult:
    """Convergence analysis payload produced from update streams."""

    convergence_metrics: ConvergenceMetricsResult
    prediction: ConvergencePredictionResult
    delta_norm_history: list[float]
    delta_mean_vector_norm_history: list[float | None]
    delta_mean_vector_window: int
    recent_delta_norms: list[float]
    recent_step_sizes: list[float]
    required_delta_norms: int
    available_delta_norms: int
    available_updates: int
    pending_updates: int
    total_updates_observed: int
    num_updates_analyzed: int
    mobility_gain_ak: list[float]
    mobility_variant_indices: list[int]
    convergence_probability_history: list[float]
    convergence_confidence_history: list[float]
    convergence_history_indices: list[int]
    message: str | None = None


def compute_convergence_analysis(updates: Sequence[UpdatePayload]) -> ConvergenceAnalysisResult:
    """Compute SPSA convergence analysis from update entries."""

    total_updates_observed = len(updates)

    def _extract_delta_vector(entry: UpdatePayload) -> dict[str, float] | None:
        raw_vector = entry.get("deltas")
        if not isinstance(raw_vector, Mapping):
            return None
        vector: dict[str, float] = {}
        for key, value in raw_vector.items():
            coerced = coerce_float(value)
            if coerced is None or not math.isfinite(coerced):
                continue
            vector[str(key)] = coerced
        if not vector:
            return None
        return vector

    def _extract_numeric_values(entry: UpdatePayload) -> tuple[float, float, dict[str, float] | None] | None:
        delta_value = coerce_float(entry.get("delta_norm"))
        step_value = coerce_float(entry.get("step"))
        delta_vector = _extract_delta_vector(entry)
        if delta_value is None or step_value is None:
            return None
        if not math.isfinite(delta_value) or not math.isfinite(step_value):
            return None
        return delta_value, step_value, delta_vector

    completed_updates: list[tuple[UpdatePayload, float, float, dict[str, float] | None]] = []
    pending_updates = 0
    for update in updates:
        extracted = _extract_numeric_values(update)
        if extracted is None:
            pending_updates += 1
            continue
        completed_updates.append((update, extracted[0], extracted[1], extracted[2]))

    num_updates_available = len(completed_updates)

    def _compute_trend(series: Sequence[float]) -> tuple[float, float, float]:
        if not series:
            return 0.0, 0.0, 0.0
        mean_val = statistics.mean(series)
        std_val = statistics.stdev(series) if len(series) > 1 else 0.0
        if len(series) > 1:
            x_vals = list(range(len(series)))
            x_mean = statistics.mean(x_vals)
            y_mean = mean_val
            numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, series, strict=False))
            denominator = sum((x - x_mean) ** 2 for x in x_vals)
            slope = numerator / denominator if denominator != 0 else 0.0
        else:
            slope = 0.0
        return mean_val, std_val, slope

    def _estimate_probability_confidence(slope: float, has_observation: bool) -> tuple[float, float]:
        confidence = max(0.0, min(100.0, (1 - abs(slope)) * 100.0))
        if slope < 0:
            probability = max(0.0, min(1.0, (1 - abs(slope)) * 0.8))
        else:
            probability = 0.1 if has_observation else 0.0
        return probability, confidence

    update_indices_all: list[int] = []
    probability_series_all: list[float] = []
    confidence_series_all: list[float] = []
    delta_series_all: list[float] = []
    mean_vector_norm_history: list[float | None] = []
    step_series_all: list[float] = []
    mobility_gain_ak: list[float] = []
    mobility_indices: list[int] = []
    recent_window = 20

    def _compute_mean_vector_norm(vectors: Sequence[dict[str, float]]) -> float:
        if not vectors:
            return 0.0
        component_sums: dict[str, float] = {}
        component_counts: dict[str, int] = {}
        for vector in vectors:
            for key, value in vector.items():
                component_sums[key] = component_sums.get(key, 0.0) + value
                component_counts[key] = component_counts.get(key, 0) + 1
        if not component_sums:
            return 0.0
        mean_components = [component_sums[key] / component_counts[key] for key in component_sums]
        return math.sqrt(sum(value * value for value in mean_components))

    vector_window: list[dict[str, float] | None] = []

    for entry, delta_value, step_value, delta_vector in completed_updates:
        idx_value = coerce_int(entry.get("update_idx"))
        if idx_value is None:
            idx_value = len(update_indices_all)
        update_indices_all.append(idx_value)
        delta_series_all.append(delta_value)
        step_series_all.append(step_value)
        gain_ak = coerce_float(entry.get("a_k"))
        if gain_ak is not None and math.isfinite(gain_ak):
            mobility_gain_ak.append(gain_ak)
            mobility_indices.append(idx_value)

        window = delta_series_all[-recent_window:]
        _, _, slope = _compute_trend(window)
        probability, confidence = _estimate_probability_confidence(slope, len(window) > 0)
        probability_series_all.append(probability)
        confidence_series_all.append(confidence)

        vector_window.append(delta_vector)
        if len(vector_window) > recent_window:
            vector_window.pop(0)
        non_null_vectors = [vector for vector in vector_window if vector]
        if non_null_vectors:
            mean_vector_norm_history.append(_compute_mean_vector_norm(non_null_vectors))
        else:
            mean_vector_norm_history.append(None)

    recent_updates = completed_updates[-100:] if num_updates_available >= 100 else completed_updates
    recent_len = len(recent_updates)
    recent_indices = update_indices_all[-recent_len:] if recent_len else []
    probability_history = probability_series_all[-recent_len:] if recent_len else []
    confidence_history = confidence_series_all[-recent_len:] if recent_len else []

    delta_norms: list[float] = [delta for _, delta, _, _ in recent_updates]
    step_sizes: list[float] = [step for _, _, step, _ in recent_updates]
    if recent_len:
        vector_norms_recent = [value for value in mean_vector_norm_history[-recent_len:] if value is not None]
    else:
        vector_norms_recent = []

    min_delta_samples = 1
    available_delta_samples = len(delta_norms)
    min_delta_samples = max(1, min_delta_samples)
    if available_delta_samples < min_delta_samples:
        return ConvergenceAnalysisResult(
            convergence_metrics=ConvergenceMetricsResult(),
            prediction=ConvergencePredictionResult(),
            delta_norm_history=[],
            delta_mean_vector_norm_history=[],
            delta_mean_vector_window=recent_window,
            recent_delta_norms=[],
            recent_step_sizes=[],
            required_delta_norms=min_delta_samples,
            available_delta_norms=available_delta_samples,
            available_updates=num_updates_available,
            pending_updates=pending_updates,
            total_updates_observed=total_updates_observed,
            num_updates_analyzed=num_updates_available,
            mobility_gain_ak=[],
            mobility_variant_indices=[],
            convergence_probability_history=probability_history,
            convergence_confidence_history=confidence_history,
            convergence_history_indices=recent_indices,
        )

    convergence_metrics = ConvergenceMetricsResult()
    prediction = ConvergencePredictionResult()

    recent_mean_vector_norm: float | None = None
    for value in reversed(mean_vector_norm_history):
        if value is not None:
            recent_mean_vector_norm = value
            break

    if len(delta_norms) >= recent_window:
        window = delta_norms[-recent_window:]
        recent_avg, recent_std, trend_slope = _compute_trend(window)
        probability, confidence = _estimate_probability_confidence(trend_slope, len(window) > 0)
        overall_avg = statistics.mean(delta_norms)
        convergence_metrics = ConvergenceMetricsResult(
            recent_avg_delta_norm=recent_avg,
            overall_avg_delta_norm=overall_avg,
            recent_std_delta_norm=recent_std,
            trend_slope=trend_slope,
            is_converging=trend_slope < 0 and recent_std < overall_avg * 0.1,
            convergence_confidence=confidence,
            recent_mean_vector_delta_norm=recent_mean_vector_norm,
            recent_avg_mean_vector_delta_norm=statistics.mean(vector_norms_recent) if vector_norms_recent else None,
        )
        if trend_slope < 0:
            projected_remaining = max(0.0, recent_avg / max(abs(trend_slope), 1e-6))
            prediction = ConvergencePredictionResult(
                remaining_updates_estimate=projected_remaining,
                convergence_probability=probability,
            )
    else:
        convergence_metrics = ConvergenceMetricsResult(
            recent_avg_delta_norm=statistics.mean(delta_norms) if delta_norms else 0.0,
            recent_std_delta_norm=statistics.stdev(delta_norms) if len(delta_norms) > 1 else 0.0,
            trend_slope=0.0,
            is_converging=False,
            convergence_confidence=0.0,
            recent_mean_vector_delta_norm=recent_mean_vector_norm,
        )

    if prediction.convergence_probability is None:
        prediction = replace(
            prediction,
            convergence_probability=probability_history[-1] if probability_history else 0.0,
        )

    return ConvergenceAnalysisResult(
        convergence_metrics=convergence_metrics,
        prediction=prediction,
        delta_norm_history=delta_series_all,
        delta_mean_vector_norm_history=mean_vector_norm_history,
        delta_mean_vector_window=recent_window,
        recent_delta_norms=delta_norms[-recent_window:],
        recent_step_sizes=step_sizes[-recent_window:],
        required_delta_norms=min_delta_samples,
        available_delta_norms=available_delta_samples,
        available_updates=num_updates_available,
        pending_updates=pending_updates,
        total_updates_observed=total_updates_observed,
        num_updates_analyzed=num_updates_available,
        mobility_gain_ak=mobility_gain_ak,
        mobility_variant_indices=mobility_indices,
        convergence_probability_history=probability_history,
        convergence_confidence_history=confidence_history,
        convergence_history_indices=recent_indices,
    )
