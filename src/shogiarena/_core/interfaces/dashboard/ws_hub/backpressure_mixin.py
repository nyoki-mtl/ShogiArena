"""Backpressure handling mixin for the dashboard WebSocket hub."""

from __future__ import annotations

import asyncio
import logging

from shogiarena._core.interfaces.dashboard.ws_hub.hub_runtime_models import LiveWsClient
from shogiarena._core.interfaces.dashboard.ws_hub.topic_policy import (
    extract_envelope_topic,
    is_assignment_topic,
    is_coalescible_topic,
    is_strict_moves_topic,
    priority_for_topic,
)
from shogiarena._core.shared.kernel.json_types import JsonValue

logger = logging.getLogger(__name__)


class WsHubBackpressureMixin:
    clients: set[LiveWsClient]
    _analysis_buffer: dict[str, tuple[JsonValue, int | None]]
    _analysis_flush_task: asyncio.Task[None] | None
    _drop_counts: dict[str, int]

    def _build_envelope(self, topic: str, payload: JsonValue) -> str | None:
        raise NotImplementedError

    def _fanout(self, message: str, topic: str, worker_idx: int | None, *, is_analysis: bool = False) -> None:
        priority = priority_for_topic(topic, message)
        for client in list(self.clients):
            if worker_idx is not None and client.worker_filter is not None and worker_idx not in client.worker_filter:
                continue
            if client.subscriptions is not None and topic not in client.subscriptions:
                continue
            if is_analysis and not client.should_include_analysis:
                continue
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                # high priority (move/clock) は drop せず、まずは queue を圧縮して最新を通す。
                if priority <= 2:
                    if is_assignment_topic(topic):
                        if self._compact_queue_for_high_priority(
                            client, incoming_topic=topic, incoming_message=message
                        ):
                            continue
                        self._disconnect_overloaded_client(
                            client,
                            topic=topic,
                            reason="assignment_queue_full_disconnect",
                        )
                        continue
                    if is_strict_moves_topic(topic):
                        self._disconnect_overloaded_client(client, topic=topic, reason="moves_queue_full_disconnect")
                        continue
                    if self._compact_queue_for_high_priority(client, incoming_topic=topic, incoming_message=message):
                        continue
                    self._record_drop(topic, "high_priority_queue_full")
                    logger.warning("WebSocket queue full; dropping high-priority topic=%s", topic)
                    continue
                # 低優先度は静かにドロップして backlog を防ぐ
                drop_reason = "low_priority_queue_full" if priority >= 4 else "queue_full"
                self._record_drop(topic, drop_reason)
                logger.debug("Dropping low-priority WS message topic=%s (reason=%s)", topic, drop_reason)

    def _compact_queue_for_high_priority(
        self, client: LiveWsClient, *, incoming_topic: str, incoming_message: str
    ) -> bool:
        maxsize = client.queue.maxsize
        if maxsize <= 0:
            return False

        drained: list[str] = []
        while True:
            try:
                item = client.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                continue
            drained.append(item)

        if not drained:
            try:
                client.queue.put_nowait(incoming_message)
                return True
            except asyncio.QueueFull:
                return False

        parsed: list[tuple[str, str | None, int]] = []
        for item in drained:
            item_topic = extract_envelope_topic(item)
            priority = priority_for_topic(item_topic or "", item)
            parsed.append((item, item_topic, priority))

        # 1) Drop low priority backlog to make room for the incoming high-priority message.
        kept = [(msg, topic, prio) for (msg, topic, prio) in parsed if prio <= 2]
        dropped_low = len(parsed) - len(kept)
        if dropped_low:
            self._record_drop(incoming_topic, "compacted_low_priority", dropped_low)

        # 2) If still too large, coalesce worker diffs/snapshots (keep only latest per topic).
        if len(kept) >= maxsize:
            seen: set[str] = set()
            coalesced_rev: list[tuple[str, str | None, int]] = []
            coalesced_drops = 0
            for msg, topic, prio in reversed(kept):
                if topic and is_coalescible_topic(topic):
                    if topic in seen:
                        coalesced_drops += 1
                        continue
                    seen.add(topic)
                coalesced_rev.append((msg, topic, prio))
            kept = list(reversed(coalesced_rev))
            if coalesced_drops:
                self._record_drop(incoming_topic, "compacted_worker_latest", coalesced_drops)

        # 3) As a last resort, trim oldest high-priority messages to ensure space.
        if len(kept) >= maxsize:
            trim_to = max(0, maxsize - 1)
            if trim_to <= 0:
                # Nothing can be kept alongside the incoming message.
                self._disconnect_overloaded_client(client, topic=incoming_topic, reason="compacted_trim_disconnect")
                return True

            strict_moves: list[tuple[str, str | None, int]] = [
                (msg, topic, prio) for (msg, topic, prio) in kept if topic and is_strict_moves_topic(topic)
            ]
            non_strict: list[tuple[str, str | None, int]] = [
                (msg, topic, prio) for (msg, topic, prio) in kept if not (topic and is_strict_moves_topic(topic))
            ]

            # Never drop strict move messages. Prefer dropping other backlog first.
            if len(strict_moves) >= trim_to:
                self._disconnect_overloaded_client(
                    client,
                    topic=incoming_topic,
                    reason="compacted_trim_disconnect_moves_backlog",
                )
                return True

            # Keep the newest non-strict messages while preserving all strict moves.
            remaining = trim_to - len(strict_moves)
            kept = non_strict[-remaining:] + strict_moves
            trimmed = max(0, len(parsed) - len(kept))
            if trimmed:
                self._record_drop(incoming_topic, "compacted_trim", trimmed)

        for msg, _topic, _prio in kept:
            try:
                client.queue.put_nowait(msg)
            except asyncio.QueueFull:
                # Unexpected, but do not loop forever.
                break

        try:
            client.queue.put_nowait(incoming_message)
            return True
        except asyncio.QueueFull:
            return False

    def _disconnect_overloaded_client(self, client: LiveWsClient, *, topic: str, reason: str) -> None:
        # If a client cannot keep up with strict move updates, it's safer to disconnect and let it
        # reconnect/rehydrate via snapshots than to drop moves and permanently corrupt history.
        queue_size_before = client.queue.qsize()
        self.clients.discard(client)
        drained = 0
        while True:
            try:
                item = client.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            drained += 1
            if item is None:
                break
        self._record_drop(topic, reason, max(1, drained))
        logger.warning(
            "WS disconnected overloaded client_id=%s topic=%s reason=%s queue_before=%s drained=%s",
            client.client_id,
            topic,
            reason,
            queue_size_before,
            drained,
        )
        try:
            client.queue.put_nowait(None)
        except asyncio.QueueFull:
            # Best effort: we already removed the client from fanout.
            pass

    def _schedule_analysis_flush(self) -> None:
        if self._analysis_flush_task and not self._analysis_flush_task.done():
            return

        async def _flush() -> None:
            # 200ms ウィンドウで最新のみ送出
            await asyncio.sleep(0.2)
            buffer = self._analysis_buffer
            self._analysis_buffer = {}
            for topic, packed in buffer.items():
                payload, worker_idx = packed
                message = self._build_envelope(topic, payload)
                if message is None:
                    continue
                self._fanout(message, topic, worker_idx, is_analysis=True)

        self._analysis_flush_task = asyncio.create_task(_flush())

    def _record_drop(self, topic: str, reason: str, count: int = 1) -> None:
        if count <= 0:
            return
        key = f"{reason}:{topic}"
        self._drop_counts[key] = self._drop_counts.get(key, 0) + count
        if self._drop_counts[key] % 100 == 1:
            logger.debug("WS drop stats %s=%s", key, self._drop_counts[key])


__all__ = ["WsHubBackpressureMixin"]
