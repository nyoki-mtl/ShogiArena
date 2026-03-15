"""Builders that map SPSA analysis results into dashboard payload contracts."""

from __future__ import annotations

from collections.abc import Sequence

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    ConvergenceAnalysis,
    ConvergenceMetrics,
    ConvergencePrediction,
    CorrelationAnalysis,
    MobilitySeriesPayload,
    ParameterTimelineEntry,
    UpdateEntry,
)
from shogiarena._core.contexts.spsa.application.dashboard.analysis import (
    ConvergenceAnalysisResult,
    ConvergenceMetricsResult,
    ConvergencePredictionResult,
    CorrelationAnalysisResult,
    ParameterTimelinePoint,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject


def normalize_updates_for_analysis(updates: Sequence[UpdateEntry]) -> list[JsonObject]:
    """Convert typed update entries into analysis input payloads."""
    return [to_json_object(update) for update in updates]


def _build_parameter_timeline_payload(
    timeline: dict[str, list[ParameterTimelinePoint]],
) -> dict[str, list[ParameterTimelineEntry]]:
    payload: dict[str, list[ParameterTimelineEntry]] = {}
    for name, points in timeline.items():
        payload[name] = [
            ParameterTimelineEntry(
                update_idx=point.update_idx,
                actual=point.actual,
                baseline=point.baseline,
                is_pending=point.is_pending,
                is_ltc_invalidated=point.is_ltc_invalidated,
                ltc_decision=point.ltc_decision,
            )
            for point in points
        ]
    return payload


def _build_convergence_metrics_payload(metrics: ConvergenceMetricsResult) -> ConvergenceMetrics:
    payload = ConvergenceMetrics()
    if metrics.recent_avg_delta_norm is not None:
        payload["recent_avg_delta_norm"] = metrics.recent_avg_delta_norm
    if metrics.overall_avg_delta_norm is not None:
        payload["overall_avg_delta_norm"] = metrics.overall_avg_delta_norm
    if metrics.recent_std_delta_norm is not None:
        payload["recent_std_delta_norm"] = metrics.recent_std_delta_norm
    if metrics.trend_slope is not None:
        payload["trend_slope"] = metrics.trend_slope
    if metrics.is_converging is not None:
        payload["is_converging"] = metrics.is_converging
    if metrics.convergence_confidence is not None:
        payload["convergence_confidence"] = metrics.convergence_confidence
    if metrics.recent_mean_vector_delta_norm is not None:
        payload["recent_mean_vector_delta_norm"] = metrics.recent_mean_vector_delta_norm
    if metrics.recent_avg_mean_vector_delta_norm is not None:
        payload["recent_avg_mean_vector_delta_norm"] = metrics.recent_avg_mean_vector_delta_norm
    return payload


def _build_convergence_prediction_payload(prediction: ConvergencePredictionResult) -> ConvergencePrediction:
    payload = ConvergencePrediction()
    if prediction.remaining_updates_estimate is not None:
        payload["remaining_updates_estimate"] = prediction.remaining_updates_estimate
    if prediction.convergence_probability is not None:
        payload["convergence_probability"] = prediction.convergence_probability
    return payload


def build_correlation_analysis_payload(analysis: CorrelationAnalysisResult) -> CorrelationAnalysis:
    payload = CorrelationAnalysis(
        correlations=analysis.correlations,
        parameter_evolution=analysis.parameter_evolution,
        gradient_evolution=analysis.gradient_evolution,
        step_evolution=analysis.step_evolution,
        parameter_names=analysis.parameter_names,
        num_updates=analysis.num_updates,
        parameter_timeline=_build_parameter_timeline_payload(analysis.parameter_timeline),
    )
    if analysis.message is not None:
        payload["message"] = analysis.message
    return payload


def build_convergence_analysis_payload(analysis: ConvergenceAnalysisResult) -> ConvergenceAnalysis:
    payload = ConvergenceAnalysis(
        convergence_metrics=_build_convergence_metrics_payload(analysis.convergence_metrics),
        prediction=_build_convergence_prediction_payload(analysis.prediction),
        delta_norm_history=analysis.delta_norm_history,
        delta_mean_vector_norm_history=analysis.delta_mean_vector_norm_history,
        delta_mean_vector_window=analysis.delta_mean_vector_window,
        recent_delta_norms=analysis.recent_delta_norms,
        recent_step_sizes=analysis.recent_step_sizes,
        required_delta_norms=analysis.required_delta_norms,
        available_delta_norms=analysis.available_delta_norms,
        available_updates=analysis.available_updates,
        pending_updates=analysis.pending_updates,
        total_updates_observed=analysis.total_updates_observed,
        num_updates_analyzed=analysis.num_updates_analyzed,
        mobility_series=MobilitySeriesPayload(
            gain_ak=analysis.mobility_gain_ak,
            variant_indices=analysis.mobility_variant_indices,
        ),
        convergence_probability_history=analysis.convergence_probability_history,
        convergence_confidence_history=analysis.convergence_confidence_history,
        convergence_history_indices=analysis.convergence_history_indices,
    )
    if analysis.message is not None:
        payload["message"] = analysis.message
    return payload
