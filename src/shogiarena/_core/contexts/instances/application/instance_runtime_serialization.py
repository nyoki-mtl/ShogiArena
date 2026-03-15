"""Serialization helpers for instance runtime models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shogiarena._core.shared.kernel.json_types import JsonObject

from .instance_config_serialization import serialize_instance_config
from .instance_models import Instance, InstanceMetrics


class _InstanceMetricsOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: float = Field(validation_alias="sampled_ts_sec")
    reachable: bool = Field(validation_alias="is_reachable")
    load_avg_1: float | None = None
    load_avg_5: float | None = None
    load_avg_15: float | None = None
    cpu_usage_pct: float | None = None
    cpu_model: str | None = None
    cpu_count: int | None = None
    cpu_usage_pct_per_core: list[float] | None = None
    mem_total_mb: int | None = None
    mem_free_mb: int | None = None
    mem_used_mb: int | None = None
    mem_used_pct: float | None = None
    in_use_slots: int = 0
    in_use_engines: int = 0
    engine_processes: int = 0
    latency_avg_ms: float | None = None
    latency_recent_ms: float | None = None
    latency_samples: int = 0
    latency_alert: bool = Field(default=False, validation_alias="has_latency_alert")
    latency_threshold_ms: int | None = None
    network_rtt_avg_ms: float | None = None
    network_rtt_recent_ms: float | None = None
    network_rtt_samples: int = 0


def serialize_instance_metrics(metrics: InstanceMetrics) -> JsonObject:
    payload = _InstanceMetricsOutput.model_validate(metrics, from_attributes=True)
    return payload.model_dump(mode="json")


def serialize_instance(instance: Instance) -> JsonObject:
    slot_limit = instance.effective_slots
    engine_capacity = instance.max_engine_capacity
    available_slots = None if slot_limit is None else max(0, slot_limit - instance.metrics.in_use_slots)
    engine_limit = None if not instance.is_engine_capacity_known else engine_capacity
    available_engines = None if engine_limit is None else max(0, engine_capacity - instance.metrics.in_use_engines)
    return {
        "id": instance.name,
        "type": instance.type.value,
        "config": serialize_instance_config(instance.config),
        "metrics": serialize_instance_metrics(instance.metrics),
        "drain": instance.is_draining,
        "last_seen": instance.last_seen_ts_sec,
        "available_slots": available_slots,
        "can_accept_job": instance.can_accept_job,
        "active_games": [game.to_dict() for game in instance.active_game_by_id.values()],
        "config_path": str(instance.source_path) if instance.source_path else None,
        "is_local": instance.is_local,
        "is_ssh": instance.is_ssh,
        "engine_limit": engine_limit,
        "available_engines": available_engines,
        "slot_capacity": slot_limit,
    }


__all__ = ["serialize_instance", "serialize_instance_metrics"]
