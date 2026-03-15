"""Topic classification and prioritization policy for WebSocket events."""

from __future__ import annotations

import json
from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_types import JsonValue


def is_analysis(topic: str, payload: JsonValue) -> bool:
    if "analysis" in topic:
        return True
    if isinstance(payload, Mapping):
        payload_map = {str(key): value for key, value in payload.items()}
        if payload_map.get("kind") == "analysis":
            return True
        inner = payload_map.get("payload")
        if isinstance(inner, Mapping):
            inner_map = {str(key): value for key, value in inner.items()}
            if inner_map.get("kind") == "analysis":
                return True
    return False


def priority_for_topic(topic: str, message: str) -> int:
    if is_assignment_topic(topic):
        return 1
    if "analysis" in topic:
        return 5
    if topic.startswith("live.engine."):
        return 6
    if topic.startswith("live.games"):
        return 4
    if "summary" in topic:
        return 3
    if "clock" in message:
        return 2
    return 1


def is_strict_moves_topic(topic: str) -> bool:
    # Design A: moves stream is an append-only log; losing a single message breaks history reconstruction.
    return topic.startswith("live.game.") and ".moves." in topic


def should_bootstrap_on_subscribe(topic: str) -> bool:
    if topic.startswith("live.summary.snapshot."):
        return True
    if topic.endswith(".snapshot"):
        return True
    if topic in {
        "live.games.delta",
        "live.assignment.snapshot",
    }:
        return True
    if topic.startswith("live.game."):
        return False
    return True


def is_assignment_topic(topic: str) -> bool:
    return topic.startswith("live.assignment.") and (topic.endswith(".diff") or topic.endswith(".snapshot"))


def extract_envelope_topic(serialized: str) -> str | None:
    try:
        data = json.loads(serialized)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    topic = data.get("topic")
    return topic if isinstance(topic, str) and topic else None


def is_coalescible_topic(topic: str) -> bool:
    # Coalesce topics that are safe to "keep only the latest" during backpressure.
    #
    # IMPORTANT:
    # - `live.game.<gid>.moves.*` is an append-only log (missing one entry breaks the client),
    #   so it must NOT be coalesced.
    # - meta/clock/engine_status/analysis/snapshot are state-like; coalescing is acceptable.
    if topic.startswith("live.worker.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
        return True
    if topic.startswith("live.game."):
        if topic.endswith(".moves.diff"):
            return False
        if topic.endswith(".analysis.diff"):
            return True
        if topic.endswith(".meta.diff"):
            return True
        if topic.endswith(".clock.diff"):
            return True
        if topic.endswith(".engine_status.diff"):
            return True
        if topic.endswith(".snapshot"):
            return True
        return False
    if topic.startswith("live.assignment.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
        return True
    if topic.startswith("live.engine.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
        return True
    return False


__all__ = [
    "extract_envelope_topic",
    "is_analysis",
    "is_assignment_topic",
    "is_coalescible_topic",
    "is_strict_moves_topic",
    "priority_for_topic",
    "should_bootstrap_on_subscribe",
]
