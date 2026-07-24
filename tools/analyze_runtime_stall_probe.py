#!/usr/bin/env python3
"""Summarize and correlate runtime-stall probe JSONL events."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values) if values else None,
        "mean_ms": statistics.fmean(values) if values else None,
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            payload = json.loads(raw_line)
            if not isinstance(payload, dict) or "event" not in payload or "mono_ns" not in payload:
                raise ValueError(f"invalid event at line {line_number}")
            events.append(payload)
    return events


def analyze(events: list[dict[str, Any]], *, severe_lag_ms: float, window_ms: float) -> dict[str, Any]:
    """Return aggregate distributions and evidence around severe loop stalls."""
    durations: dict[str, list[float]] = defaultdict(list)
    completion_steps: dict[str, list[float]] = defaultdict(list)
    gc_by_generation: dict[str, list[float]] = defaultdict(list)
    process_samples: list[dict[str, Any]] = []
    for event in events:
        event_name = str(event["event"])
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)):
            durations[event_name].append(float(duration))
        if event_name == "completion_step" and isinstance(duration, (int, float)):
            completion_steps[str(event.get("step", "unknown"))].append(float(duration))
        if event_name == "gc_pause" and isinstance(duration, (int, float)):
            gc_by_generation[str(event.get("generation", "unknown"))].append(float(duration))
        if event_name == "process_sample":
            process_samples.append(event)

    severe_events = [
        event
        for event in events
        if event.get("event") == "loop_lag"
        and isinstance(event.get("duration_ms"), (int, float))
        and float(event["duration_ms"]) >= severe_lag_ms
    ]
    window_ns = int(window_ms * 1_000_000)
    neighborhoods: list[dict[str, Any]] = []
    relevant = {
        "gc_pause",
        "completion_lock_wait",
        "completion_lock_hold",
        "completion_step",
        "completion_total",
        "process_sample",
        "thread_overshoot",
    }
    for severe in severe_events:
        center = int(severe["mono_ns"])
        nearby = []
        for event in events:
            mono_ns = int(event["mono_ns"])
            if event.get("event") in relevant and abs(mono_ns - center) <= window_ns:
                nearby.append(
                    {
                        "delta_ms": (mono_ns - center) / 1_000_000.0,
                        "event": event.get("event"),
                        "duration_ms": event.get("duration_ms"),
                        "step": event.get("step"),
                        "game_id": event.get("game_id"),
                        "queues": event.get("queues"),
                    }
                )
        neighborhoods.append(
            {
                "mono_ns": center,
                "loop_lag_ms": severe["duration_ms"],
                "nearby": nearby,
            }
        )

    queue_max: dict[str, int] = defaultdict(int)
    rss_values: list[int] = []
    completed_max = 0
    for sample in process_samples:
        queues = sample.get("queues")
        if isinstance(queues, dict):
            for name, value in queues.items():
                if isinstance(value, int):
                    queue_max[str(name)] = max(queue_max[str(name)], value)
        rss = sample.get("rss_bytes")
        if isinstance(rss, int):
            rss_values.append(rss)
        completed = sample.get("completed_games")
        if isinstance(completed, int):
            completed_max = max(completed_max, completed)

    return {
        "schema": 1,
        "event_count": len(events),
        "completed_games": max(
            completed_max,
            max(
                (int(event["completed_games"]) for event in events if isinstance(event.get("completed_games"), int)),
                default=0,
            ),
        ),
        "loop_lag": _distribution(durations["loop_lag"]),
        "thread_overshoot": _distribution(durations["thread_overshoot"]),
        "completion_total": _distribution(durations["completion_total"]),
        "completion_lock_wait": _distribution(durations["completion_lock_wait"]),
        "completion_lock_hold": _distribution(durations["completion_lock_hold"]),
        "completion_steps": {name: _distribution(values) for name, values in sorted(completion_steps.items())},
        "gc_pause_by_generation": {
            generation: _distribution(values) for generation, values in sorted(gc_by_generation.items())
        },
        "queue_max": dict(sorted(queue_max.items())),
        "rss_min_bytes": min(rss_values) if rss_values else None,
        "rss_max_bytes": max(rss_values) if rss_values else None,
        "severe_lag_threshold_ms": severe_lag_ms,
        "severe_lag_count": len(severe_events),
        "severe_lag_neighborhoods": neighborhoods,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe_jsonl", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--severe-lag-ms", type=float, default=500.0)
    parser.add_argument("--window-ms", type=float, default=2000.0)
    return parser.parse_args()


def main() -> int:
    """Analyze one probe JSONL file."""
    args = _parse_args()
    summary = analyze(
        _load_events(args.probe_jsonl),
        severe_lag_ms=args.severe_lag_ms,
        window_ms=args.window_ms,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
