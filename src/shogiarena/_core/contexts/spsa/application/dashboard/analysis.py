"""Application-layer SPSA dashboard analysis logic."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shogiarena._core.contexts.spsa.application.dashboard.analysis_convergence import (
    ConvergenceAnalysisResult,
    ConvergenceMetricsResult,
    ConvergencePredictionResult,
    compute_convergence_analysis,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object_or_empty
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_non_negative_int,
)

UpdatePayload = Mapping[str, JsonValue]


@dataclass(slots=True, frozen=True)
class ParameterTimelinePoint:
    """Normalized timeline point for a single parameter."""

    update_idx: int
    actual: float
    baseline: float
    is_pending: bool
    is_ltc_invalidated: bool
    ltc_decision: str | None


@dataclass(slots=True, frozen=True)
class CorrelationAnalysisResult:
    """Correlation analysis payload produced from update streams."""

    correlations: dict[str, float]
    parameter_evolution: dict[str, list[float]]
    gradient_evolution: dict[str, list[float]]
    step_evolution: list[float]
    parameter_names: list[str]
    num_updates: int
    parameter_timeline: dict[str, list[ParameterTimelinePoint]]
    message: str | None = None


def compute_correlation_analysis(
    updates: Sequence[UpdatePayload],
    *,
    initial_params_override: Mapping[str, float] | None = None,
) -> CorrelationAnalysisResult:
    """Compute SPSA correlation analysis from update entries."""

    if len(updates) < 2:
        return CorrelationAnalysisResult(
            correlations={},
            parameter_evolution={},
            gradient_evolution={},
            step_evolution=[],
            parameter_names=[],
            num_updates=len(updates),
            parameter_timeline={},
            message="Insufficient data for correlation analysis",
        )

    normalized_updates: list[JsonObject] = []
    update_map: dict[int, JsonObject] = {}
    initial_params: dict[str, float] = {}

    for update in updates:
        idx = coerce_int(update.get("update_idx"))
        if idx is None:
            continue

        params = to_json_object_or_empty(update.get("params"))
        if params and idx in {-1, 0}:
            for name, value in params.items():
                if (coerced := coerce_float(value)) is not None:
                    initial_params[name] = coerced

        normalized_update = to_json_object_or_empty(update)
        existing = update_map.get(idx, {})
        update_map[idx] = {**existing, **normalized_update}

    sorted_indices = sorted(update_map.keys())
    for idx in sorted_indices:
        normalized_updates.append(update_map[idx])

    if not initial_params and normalized_updates:
        first_update = normalized_updates[0]
        params = to_json_object_or_empty(first_update.get("params"))
        for name, value in params.items():
            if (coerced := coerce_float(value)) is not None:
                initial_params[name] = coerced

    if initial_params_override is not None:
        for name, value in initial_params_override.items():
            if (coerced := coerce_float(value)) is not None:
                initial_params[name] = coerced

    param_names_set: set[str] = set()
    for update in normalized_updates:
        param_names_set.update(to_json_object_or_empty(update.get("params")).keys())
    param_names: list[str] = sorted(param_names_set)
    if initial_params:
        param_names.extend(k for k in initial_params.keys() if k not in param_names)
        param_names = sorted(param_names)

    baseline_snapshots: dict[int, dict[str, float]] = {}
    baseline_committed_snapshots: dict[int, dict[str, float]] = {}
    current_baseline: dict[str, float] = initial_params.copy()
    ltc_rejection_ranges: list[tuple[int, int]] = []
    ltc_accepted_indices: set[int] = set()

    if sorted_indices and current_baseline:
        first_idx = sorted_indices[0]
        baseline_snapshots[first_idx] = current_baseline.copy()
        baseline_committed_snapshots[first_idx] = current_baseline.copy()

    for update in normalized_updates:
        idx = coerce_int(update.get("update_idx"))
        if idx is None:
            continue

        baseline_snapshots[idx] = current_baseline.copy()

        ltc_rejected = coerce_bool(update.get("is_ltc_rejected", False))
        ltc_reverted_to_idx = coerce_non_negative_int(update.get("ltc_reverted_to"))

        ltc_info = to_json_object_or_empty(update.get("ltc_regression"))
        if ltc_info.get("is_accepted") is True:
            ltc_accepted_indices.add(idx)

        if ltc_rejected and ltc_reverted_to_idx is not None:
            baseline_idx = ltc_reverted_to_idx
            ltc_rejection_ranges.append((baseline_idx, idx))
            if baseline_idx == 0:
                current_baseline = initial_params.copy()
            else:
                reverted_baseline = baseline_committed_snapshots.get(baseline_idx) or baseline_snapshots.get(
                    baseline_idx
                )
                if reverted_baseline is not None:
                    current_baseline = reverted_baseline.copy()
        else:
            params = to_json_object_or_empty(update.get("params"))
            if params and not coerce_bool(update.get("is_pending", False)):
                for name, value in params.items():
                    if (coerced := coerce_float(value)) is not None:
                        current_baseline[name] = coerced

        baseline_committed_snapshots[idx] = current_baseline.copy()

    invalidated_indices: set[int] = set()
    superseded_reject_points: set[int] = set()
    all_reject_points = {rejected for _, rejected in ltc_rejection_ranges}
    for baseline_idx, _ in ltc_rejection_ranges:
        if baseline_idx in all_reject_points:
            superseded_reject_points.add(baseline_idx)

    for baseline_idx, rejected_idx in ltc_rejection_ranges:
        if rejected_idx <= baseline_idx:
            continue
        for idx in sorted_indices:
            if baseline_idx <= idx < rejected_idx and idx not in ltc_accepted_indices:
                invalidated_indices.add(idx)

    invalidated_indices.update(superseded_reject_points - ltc_accepted_indices)

    parameter_timeline: dict[str, list[ParameterTimelinePoint]] = {name: [] for name in param_names}

    for update in normalized_updates:
        idx = coerce_int(update.get("update_idx"))
        if idx is None:
            continue

        params = to_json_object_or_empty(update.get("params"))
        if not params:
            continue

        baseline = baseline_snapshots.get(idx, current_baseline.copy())

        raw_ltc_rejected = coerce_bool(update.get("is_ltc_rejected", False))
        ltc_reverted_to_idx = coerce_non_negative_int(update.get("ltc_reverted_to"))
        ltc_invalidated = idx in invalidated_indices
        ltc_decision: str | None = None
        ltc_info = to_json_object_or_empty(update.get("ltc_regression"))
        accepted = ltc_info.get("is_accepted")
        if accepted is True:
            ltc_decision = "accepted"
        elif accepted is False:
            ltc_decision = "rejected"
        elif raw_ltc_rejected:
            ltc_decision = "rejected"

        for name in param_names:
            param_value = coerce_float(params.get(name))
            if param_value is None:
                continue

            baseline_value = baseline.get(name, param_value)

            if raw_ltc_rejected and ltc_reverted_to_idx is not None:
                baseline_idx = ltc_reverted_to_idx
                if baseline_idx == 0:
                    revert_source = initial_params
                else:
                    revert_source = (
                        baseline_committed_snapshots.get(baseline_idx) or baseline_snapshots.get(baseline_idx) or {}
                    )
                reverted_value = coerce_float(revert_source.get(name))
                if reverted_value is not None:
                    baseline_value = reverted_value
                effective_actual = baseline_value
            else:
                effective_actual = param_value

            parameter_timeline[name].append(
                ParameterTimelinePoint(
                    update_idx=idx,
                    actual=effective_actual,
                    baseline=baseline_value,
                    is_pending=coerce_bool(update.get("is_pending", False)),
                    is_ltc_invalidated=ltc_invalidated,
                    ltc_decision=ltc_decision,
                )
            )

    def resolve_baseline_for_parameter(name: str) -> float | None:
        if (initial_value := coerce_float(initial_params.get(name))) is not None:
            return initial_value

        for idx in sorted_indices:
            snapshot = baseline_snapshots.get(idx)
            if not snapshot:
                continue
            if (snapshot_value := coerce_float(snapshot.get(name))) is not None:
                return snapshot_value

        for update in normalized_updates:
            params = to_json_object_or_empty(update.get("params"))
            if (raw_value := coerce_float(params.get(name))) is not None:
                return raw_value
        return None

    for name in param_names:
        entries = parameter_timeline.get(name)
        if entries is None:
            continue
        entries.sort(key=lambda item: item.update_idx)
        has_baseline_entry = any(item.update_idx <= 0 for item in entries)
        if not has_baseline_entry:
            baseline_value = resolve_baseline_for_parameter(name)
            if baseline_value is not None:
                entries.insert(
                    0,
                    ParameterTimelinePoint(
                        update_idx=0,
                        actual=baseline_value,
                        baseline=baseline_value,
                        is_pending=False,
                        is_ltc_invalidated=False,
                        ltc_decision=None,
                    ),
                )
        parameter_timeline[name] = entries

    param_evolution: dict[str, list[float]] = {name: [] for name in param_names}
    gradient_evolution: dict[str, list[float]] = {name: [] for name in param_names}
    step_evolution: list[float] = []

    for update in normalized_updates:
        params = to_json_object_or_empty(update.get("params"))
        grads = to_json_object_or_empty(update.get("gradients"))
        step_evolution.append(coerce_float(update.get("step")) or 0.0)
        for name in param_names:
            param_evolution[name].append(coerce_float(params.get(name)) or 0.0)
            gradient_evolution[name].append(coerce_float(grads.get(name)) or 0.0)

    correlations: dict[str, float] = {}
    for index, param1 in enumerate(param_names):
        for param2 in param_names[index + 1 :]:
            values1 = param_evolution[param1]
            values2 = param_evolution[param2]
            if len(values1) >= 2 and len(values2) >= 2:
                count = len(values1)
                sum1 = sum(values1)
                sum2 = sum(values2)
                sum1_sq = sum(value * value for value in values1)
                sum2_sq = sum(value * value for value in values2)
                sum12 = sum(x * y for x, y in zip(values1, values2, strict=False))
                numerator = count * sum12 - sum1 * sum2
                term1 = count * sum1_sq - sum1 * sum1
                term2 = count * sum2_sq - sum2 * sum2
                if term1 <= 0 or term2 <= 0:
                    continue
                denominator = math.sqrt(term1 * term2)
                if denominator != 0:
                    correlations[f"{param1}_{param2}"] = numerator / denominator

    return CorrelationAnalysisResult(
        correlations=correlations,
        parameter_evolution=param_evolution,
        gradient_evolution=gradient_evolution,
        step_evolution=step_evolution,
        parameter_names=param_names,
        num_updates=len(normalized_updates),
        parameter_timeline=parameter_timeline,
    )


__all__ = [
    "ConvergenceAnalysisResult",
    "ConvergenceMetricsResult",
    "ConvergencePredictionResult",
    "CorrelationAnalysisResult",
    "ParameterTimelinePoint",
    "compute_convergence_analysis",
    "compute_correlation_analysis",
]
