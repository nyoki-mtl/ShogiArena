"""WebSocket hub for Live View updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiohttp import web

from shogiarena._core.interfaces.dashboard.ws_hub.backpressure_mixin import (
    WsHubBackpressureMixin,
)
from shogiarena._core.interfaces.dashboard.ws_hub.control_mixin import (
    WsHubControlMixin,
)
from shogiarena._core.interfaces.dashboard.ws_hub.hub_message_models import (
    LiveBootstrapProvider,
    LiveSnapshotResolver,
)
from shogiarena._core.interfaces.dashboard.ws_hub.hub_runtime_models import (
    LiveWsClient,
    TopicState,
)
from shogiarena._core.interfaces.dashboard.ws_hub.runtime_mixin import (
    WsHubRuntimeMixin,
)
from shogiarena._core.interfaces.dashboard.ws_hub.topic_policy import (
    is_analysis,
    is_strict_moves_topic,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

logger = logging.getLogger(__name__)


def _parse_workers_param(raw_value: str | None) -> set[int] | None:
    if not raw_value:
        return None
    parsed: set[int] = set()
    for part in raw_value.split(","):
        value = part.strip()
        if not value:
            continue
        if (coerced := coerce_int(value)) is None:
            logger.warning("Invalid workers query parameter on WS request: %s", raw_value)
            return None
        parsed.add(coerced)
    return parsed or None


class LiveWebSocketHub(WsHubBackpressureMixin, WsHubControlMixin, WsHubRuntimeMixin):
    """Broadcasts LiveEnvelope messages to subscribed WebSocket clients."""

    def __init__(
        self,
        *,
        bootstrap_provider: LiveBootstrapProvider | None = None,
        snapshot_resolver: LiveSnapshotResolver | None = None,
        heartbeat_interval: float = 15.0,
        ring_size: int = 512,
        topic_ttl_seconds: float | None = None,
        max_topics: int = 5000,
        topic_prune_interval_seconds: float = 60.0,
    ) -> None:
        self.bootstrap_provider = bootstrap_provider
        self.snapshot_resolver = snapshot_resolver
        self.heartbeat_interval = heartbeat_interval
        self.clients: set[LiveWsClient] = set()
        self._topics: dict[str, TopicState] = {}
        self._ring_size = ring_size
        self._topic_ttl_ms = int(topic_ttl_seconds * 1000) if topic_ttl_seconds is not None else None
        self._max_topics = max(0, int(max_topics))
        self._topic_prune_interval_ms = max(1, int(topic_prune_interval_seconds * 1000))
        self._last_topic_prune_ms = 0
        self._is_closed = False
        self._analysis_buffer: dict[str, tuple[JsonValue, int | None]] = {}
        self._analysis_flush_task = None
        self._drop_counts: dict[str, int] = {}
        self._started_at_ms = int(time.time() * 1000)
        self._topic_subscribers: dict[str, int] = {}
        self._global_subscribers = 0
        self._client_seq = 0

    async def handler(self, request: web.Request) -> web.StreamResponse:
        """aiohttp route handler for `/ws`."""

        if self._is_closed:
            raise web.HTTPServiceUnavailable(text="Live WebSocket hub is shutting down")

        ws = web.WebSocketResponse(heartbeat=int(self.heartbeat_interval * 2))
        await ws.prepare(request)

        worker_filter = _parse_workers_param(request.query.get("workers"))
        self._client_seq += 1
        client = LiveWsClient(ws=ws, worker_filter=worker_filter, client_id=self._client_seq)
        self.clients.add(client)
        self._add_client_subscriptions(client)
        try:
            self._send_bootstrap_messages(client)
            await self._attach_tasks(client)
            return ws
        finally:
            self.clients.discard(client)
            self._remove_client_subscriptions(client)
            await self._graceful_close(client)

    def publish(self, topic: str, payload: JsonValue, *, worker_idx: int | None = None) -> None:
        """Broadcast a payload to all connected clients (respecting filters)."""

        if self._is_closed or not self.clients:
            return

        should_buffer_analysis = is_analysis(topic, payload)
        if should_buffer_analysis:
            self._analysis_buffer[topic] = (payload, worker_idx)
            self._schedule_analysis_flush()
            return

        message = self._build_envelope(topic, payload)
        if message is None:
            return
        self._fanout(message, topic, worker_idx, is_analysis=should_buffer_analysis)

    def has_subscribers(self, topic: str) -> bool:
        if self._global_subscribers > 0:
            return True
        return self._topic_subscribers.get(topic, 0) > 0

    async def shutdown(self) -> None:
        """Terminate all connections and prevent new ones from being accepted."""

        self._is_closed = True
        for client in list(self.clients):
            try:
                client.queue.put_nowait("null")
            except asyncio.QueueFull:
                logger.debug(
                    "WS shutdown signal skipped because client queue is full (client_id=%s)",
                    client.client_id,
                )
            await self._graceful_close(client)
        self.clients.clear()

    def _build_envelope(self, topic: str, payload: JsonValue) -> str | None:
        state = self._topics.setdefault(topic, TopicState())
        next_seq = state.seq + 1
        now_ms = int(time.time() * 1000)
        envelope: JsonObject = {
            "topic": topic,
            "seq": next_seq,
            "ts": now_ms,
            "payload": payload,
        }
        try:
            serialized = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self._record_drop(topic, "serialize_error")
            logger.warning("Failed to serialize WS envelope topic=%s: %s", topic, exc)
            return None
        state.seq = next_seq
        state.last_published_ms = now_ms
        self._maybe_prune_topics(now_ms)
        state.ring.append(serialized)
        while len(state.ring) > self._ring_size:
            state.ring.popleft()
        return serialized

    def _maybe_prune_topics(self, now_ms: int) -> None:
        if self._topic_ttl_ms is None and self._max_topics <= 0:
            return
        if now_ms - self._last_topic_prune_ms < self._topic_prune_interval_ms:
            return
        self._last_topic_prune_ms = now_ms

        if self._topic_ttl_ms is not None:
            threshold = now_ms - self._topic_ttl_ms
            expired = [
                name
                for name, state in self._topics.items()
                if state.last_published_ms and state.last_published_ms < threshold
            ]
            for name in expired:
                self._topics.pop(name, None)

        if self._max_topics > 0 and len(self._topics) > self._max_topics:
            # Evict oldest topics by last publish time to prevent unbounded growth.
            ordered = sorted(self._topics.items(), key=lambda kv: kv[1].last_published_ms or 0)
            to_remove = len(self._topics) - self._max_topics
            for name, _state in ordered[:to_remove]:
                self._topics.pop(name, None)

        if len(self._drop_counts) > 8192:
            # Drop stats are diagnostics-only; cap keys to avoid unbounded growth.
            self._drop_counts.clear()

    def _send_bootstrap_messages(self, client: LiveWsClient) -> None:
        if not self.bootstrap_provider:
            return
        for topic, payload in self.bootstrap_provider(client.worker_filter):
            serialized = self._build_envelope(topic, payload)
            if serialized is None:
                continue
            try:
                client.queue.put_nowait(serialized)
            except asyncio.QueueFull:
                if is_strict_moves_topic(topic):
                    self._disconnect_overloaded_client(client, topic=topic, reason="bootstrap_queue_full_disconnect")
                    return
                logger.warning("WebSocket queue full during bootstrap for topic=%s", topic)
                break

    def snapshot_diagnostics(self) -> JsonObject:
        """Return a cheap snapshot of hub state for diagnostics endpoints."""

        topics: JsonObject = {}
        for name, state in self._topics.items():
            topics[name] = {
                "seq": state.seq,
                "ring_size": len(state.ring),
            }
        drops_total = sum(self._drop_counts.values())
        drops_by_reason: dict[str, int] = {}
        drops_by_topic: dict[str, int] = {}
        for key, value in self._drop_counts.items():
            if ":" not in key:
                continue
            reason, topic = key.split(":", 1)
            drops_by_reason[reason] = drops_by_reason.get(reason, 0) + value
            drops_by_topic[topic] = drops_by_topic.get(topic, 0) + value
        now_ms = int(time.time() * 1000)
        payload: JsonObject = {
            "clients": len(self.clients),
            "topics": topics,
            "drop_counts": dict(self._drop_counts),
            "drop_total": drops_total,
            "drops_by_reason": drops_by_reason,
            "drops_by_topic": drops_by_topic,
            "started_at_ms": self._started_at_ms,
            "ts_ms": now_ms,
            "uptime_ms": max(0, now_ms - self._started_at_ms),
            "analysis_buffer": len(self._analysis_buffer),
        }
        return payload


__all__ = ["LiveWebSocketHub"]
