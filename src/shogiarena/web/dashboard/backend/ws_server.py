"""WebSocket hub for Live View updates.

This module introduces a lightweight WebSocket broadcaster for `/ws`.
It tracks connected clients and distributes LiveEnvelope messages with
per-topic sequence numbers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

logger = logging.getLogger(__name__)

LiveBootstrapProvider = Callable[[set[int] | None], Iterable[tuple[str, Any]]]
LiveSnapshotResolver = Callable[[str], Iterable[tuple[str, Any]]]


@dataclass
class TopicState:
    seq: int = 0
    ring: deque[str] = field(default_factory=deque)
    last_published_ms: int = 0


def _parse_workers_param(raw_value: str | None) -> set[int] | None:
    if not raw_value:
        return None
    try:
        parsed = {int(part.strip()) for part in raw_value.split(",") if part.strip()}
        return parsed or None
    except ValueError:
        logger.warning("Invalid workers query parameter on WS request: %s", raw_value)
        return None


@dataclass(eq=False)
class LiveWsClient:
    ws: web.WebSocketResponse
    worker_filter: set[int] | None
    client_id: int = 0
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(maxsize=1024))
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    subscriptions: set[str] | None = None
    include_analysis: bool = True
    disconnect_reason: str | None = None

    def __hash__(self) -> int:  # pragma: no cover - trivial
        return id(self)


class LiveWebSocketHub:
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
    ):
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
        self._closed = False
        self._analysis_buffer: dict[str, Any] = {}
        self._analysis_flush_task: asyncio.Task[None] | None = None
        self._drop_counts: dict[str, int] = {}
        self._started_at_ms = int(time.time() * 1000)
        self._topic_subscribers: dict[str, int] = {}
        self._global_subscribers: int = 0

        self._client_seq = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def handler(self, request: web.Request) -> web.StreamResponse:
        """aiohttp route handler for `/ws`."""

        if self._closed:
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

    def publish(self, topic: str, payload: Any, *, worker_idx: int | None = None) -> None:
        """Broadcast a payload to all connected clients (respecting filters)."""

        if self._closed or not self.clients:
            return

        is_analysis = self._is_analysis(topic, payload)

        if is_analysis:
            self._analysis_buffer[topic] = (payload, worker_idx)
            self._schedule_analysis_flush()
            return

        message = self._build_envelope(topic, payload)
        if message is None:
            return
        self._fanout(message, topic, worker_idx, is_analysis=is_analysis)

    def has_subscribers(self, topic: str) -> bool:
        if self._global_subscribers > 0:
            return True
        return self._topic_subscribers.get(topic, 0) > 0

    async def shutdown(self) -> None:
        """Terminate all connections and prevent new ones from being accepted."""

        self._closed = True
        for client in list(self.clients):
            try:
                client.queue.put_nowait("null")
            except asyncio.QueueFull:
                pass
            await self._graceful_close(client)
        self.clients.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_envelope(self, topic: str, payload: Any) -> str | None:
        state = self._topics.setdefault(topic, TopicState())
        next_seq = state.seq + 1
        now_ms = int(time.time() * 1000)
        envelope = {
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
                if self._is_strict_moves_topic(topic):
                    self._disconnect_overloaded_client(client, topic=topic, reason="bootstrap_queue_full_disconnect")
                    return
                logger.warning("WebSocket queue full during bootstrap for topic=%s", topic)
                break

    def _fanout(self, message: str, topic: str, worker_idx: int | None, *, is_analysis: bool = False) -> None:
        priority = self._priority_for_topic(topic, message)
        for client in list(self.clients):
            if worker_idx is not None and client.worker_filter is not None and worker_idx not in client.worker_filter:
                continue
            if client.subscriptions is not None and topic not in client.subscriptions:
                continue
            if is_analysis and not client.include_analysis:
                continue
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                # high priority (move/clock) は drop せず、まずは queue を圧縮して最新を通す。
                if priority <= 2:
                    if self._is_assignment_topic(topic):
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
                    if self._is_strict_moves_topic(topic):
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

    async def _attach_tasks(self, client: LiveWsClient) -> None:
        sender = asyncio.create_task(self._pump_send(client))
        receiver = asyncio.create_task(self._pump_receive(client))
        heartbeat = asyncio.create_task(self._heartbeat_loop(client))
        client.tasks.extend([sender, receiver, heartbeat])

        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc:
                logger.debug("WebSocket task ended with error: %s", exc)

    async def _graceful_close(self, client: LiveWsClient) -> None:
        for task in client.tasks:
            task.cancel()
        if not client.ws.closed:
            try:
                await client.ws.close()
            except (OSError, RuntimeError):
                pass

    async def _pump_send(self, client: LiveWsClient) -> None:
        try:
            while not client.ws.closed:
                message = await client.queue.get()
                if message is None or message == "null":
                    break
                await client.ws.send_str(message)
        except ConnectionResetError:
            logger.debug("WebSocket send loop terminated: connection reset")
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError) as exc:
            logger.debug("WebSocket send loop terminated: %s", exc)

    async def _pump_receive(self, client: LiveWsClient) -> None:
        async for msg in client.ws:
            if msg.type == WSMsgType.TEXT:
                await self._handle_client_message(client, msg.data)
            elif msg.type == WSMsgType.ERROR:
                logger.debug("WebSocket error: %s", client.ws.exception())
                break

    async def _handle_client_message(self, client: LiveWsClient, raw: str) -> None:
        text = raw.strip()
        if text.lower() == "ping":
            serialized = self._build_envelope("live.heartbeat", {"pong": True})
            if serialized is not None:
                await client.ws.send_str(serialized)
            return

        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON WS message")
            return

        if not isinstance(message, dict):
            return

        msg_type = str(message.get("type") or "").lower()
        if msg_type == "request_snapshot":
            topic = message.get("topic")
            if isinstance(topic, str) and self.snapshot_resolver:
                from_seq_raw = message.get("fromSeq")
                from_seq = int(from_seq_raw) if isinstance(from_seq_raw, int | float) else None
                if from_seq is not None and from_seq >= 0:
                    self._replay_from_seq(client, topic, from_seq)
                reply_count = 0
                for reply_topic, payload in self.snapshot_resolver(topic):
                    if payload is None:
                        continue
                    reply_count += 1
                    serialized = self._build_envelope(reply_topic, payload)
                    if serialized is None:
                        continue
                    try:
                        client.queue.put_nowait(serialized)
                    except asyncio.QueueFull:
                        # If snapshot replies cannot be queued, the client is overloaded.
                        # For strict move recovery, disconnect and let the client reconnect cleanly.
                        if self._is_strict_moves_topic(topic) or self._is_strict_moves_topic(reply_topic):
                            self._disconnect_overloaded_client(
                                client,
                                topic=reply_topic,
                                reason="snapshot_reply_queue_full_disconnect",
                            )
                            return
                        logger.debug("WS queue full while replying snapshot topic=%s", reply_topic)

        elif msg_type in {"ack", "subscribe", "unsubscribe", "set_worker_filter"}:
            self._handle_control_message(client, msg_type, message)
            return

    def _add_client_subscriptions(self, client: LiveWsClient) -> None:
        subs = client.subscriptions
        if subs is None:
            self._global_subscribers += 1
            return
        for topic in subs:
            self._topic_subscribers[topic] = self._topic_subscribers.get(topic, 0) + 1

    def _remove_client_subscriptions(self, client: LiveWsClient) -> None:
        subs = client.subscriptions
        if subs is None:
            self._global_subscribers = max(0, self._global_subscribers - 1)
            return
        for topic in subs:
            current = self._topic_subscribers.get(topic, 0)
            if current <= 1:
                self._topic_subscribers.pop(topic, None)
            else:
                self._topic_subscribers[topic] = current - 1

    def _set_client_subscriptions(self, client: LiveWsClient, next_subs: set[str] | None) -> None:
        if client.subscriptions == next_subs:
            return
        self._remove_client_subscriptions(client)
        client.subscriptions = next_subs
        self._add_client_subscriptions(client)

    def _handle_control_message(self, client: LiveWsClient, msg_type: str, message: dict[str, Any]) -> None:
        include_analysis = message.get("includeAnalysis")
        if isinstance(include_analysis, bool):
            client.include_analysis = include_analysis

        if msg_type == "set_worker_filter":
            workers = message.get("workers")
            if workers is None:
                client.worker_filter = None
                return
            if isinstance(workers, list):
                parsed: set[int] = set()
                for raw in workers:
                    try:
                        value = int(raw)
                    except (TypeError, ValueError):
                        continue
                    parsed.add(value)
                client.worker_filter = parsed
            return

        if msg_type == "subscribe":
            if "topics" not in message:
                return
            topics = message.get("topics")
            if topics is None:
                self._set_client_subscriptions(client, None)
                return
            if isinstance(topics, list):
                valid = {str(t) for t in topics if isinstance(t, str) and t}
                self._set_client_subscriptions(client, valid or None)
                # 初回購読時に最新スナップショットを即送出し、ギャップを埋める
                if client.subscriptions and self.snapshot_resolver:
                    for topic in client.subscriptions:
                        if not self._should_bootstrap_on_subscribe(topic):
                            continue
                        for reply_topic, payload in self.snapshot_resolver(topic):
                            if payload is None:
                                continue
                            serialized = self._build_envelope(reply_topic, payload)
                            if serialized is None:
                                continue
                            try:
                                client.queue.put_nowait(serialized)
                            except asyncio.QueueFull:
                                if self._is_strict_moves_topic(topic) or self._is_strict_moves_topic(reply_topic):
                                    self._disconnect_overloaded_client(
                                        client,
                                        topic=reply_topic,
                                        reason="subscribe_bootstrap_queue_full_disconnect",
                                    )
                                    return
                                self._record_drop(reply_topic, "queue_full_bootstrap")
                                logger.debug(
                                    "WS queue full while sending subscription bootstrap topic=%s",
                                    reply_topic,
                                )
            return
        if msg_type == "unsubscribe":
            if "topics" not in message:
                self._set_client_subscriptions(client, set())
                return
            topics = message.get("topics")
            if topics is None:
                self._set_client_subscriptions(client, set())
                return
            if isinstance(topics, list):
                if client.subscriptions is None:
                    # if previously "all", convert to empty set then remove
                    self._set_client_subscriptions(client, set())
                    return
                next_subs = set(client.subscriptions)
                for t in topics:
                    if isinstance(t, str):
                        next_subs.discard(t)
                self._set_client_subscriptions(client, next_subs)
            return
        if msg_type == "ack":
            # For now we just accept; could track latency later.
            return

    def _replay_from_seq(self, client: LiveWsClient, topic: str, from_seq: int) -> int:
        state = self._topics.get(topic)
        if not state or not state.ring:
            return 0
        replayed = 0
        for serialized in state.ring:
            try:
                data = json.loads(serialized)
            except json.JSONDecodeError:
                continue
            seq_val = data.get("seq")
            if not isinstance(seq_val, int | float):
                continue
            if seq_val > from_seq:
                try:
                    client.queue.put_nowait(serialized)
                    replayed += 1
                except asyncio.QueueFull:
                    if self._is_strict_moves_topic(topic):
                        self._disconnect_overloaded_client(client, topic=topic, reason="replay_queue_full_disconnect")
                        return replayed
                    logger.debug("WS queue full while replaying topic=%s", topic)
                    break
        return replayed

    async def _heartbeat_loop(self, client: LiveWsClient) -> None:
        try:
            while not client.ws.closed:
                await asyncio.sleep(self.heartbeat_interval)
                try:
                    serialized = self._build_envelope("live.heartbeat", {})
                    if serialized is None:
                        continue
                    client.queue.put_nowait(serialized)
                except asyncio.QueueFull:
                    logger.debug("Heartbeat skipped due to full queue")
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            pass

    def _is_analysis(self, topic: str, payload: Any) -> bool:
        if "analysis" in topic:
            return True
        if isinstance(payload, Mapping):
            if payload.get("kind") == "analysis":
                return True
            inner = payload.get("payload")
            if isinstance(inner, Mapping) and inner.get("kind") == "analysis":
                return True
        return False

    def _priority_for_topic(self, topic: str, message: str) -> int:
        if self._is_assignment_topic(topic):
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

    @staticmethod
    def _is_strict_moves_topic(topic: str) -> bool:
        # Design A: moves stream is an append-only log; losing a single message breaks history reconstruction.
        return topic.startswith("live.game.") and ".moves." in topic

    @staticmethod
    def _should_bootstrap_on_subscribe(topic: str) -> bool:
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

    @staticmethod
    def _is_assignment_topic(topic: str) -> bool:
        return topic.startswith("live.assignment.") and (topic.endswith(".diff") or topic.endswith(".snapshot"))

    @staticmethod
    def _extract_envelope_topic(serialized: str) -> str | None:
        try:
            data = json.loads(serialized)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        topic = data.get("topic")
        return topic if isinstance(topic, str) and topic else None

    @staticmethod
    def _is_coalescible_topic(topic: str) -> bool:
        # Coalesce topics that are safe to "keep only the latest" during backpressure.
        #
        # IMPORTANT:
        # - `live.game.<gid>.moves.*` is an append-only log (missing one entry breaks the client),
        #   so it must NOT be coalesced.
        # - `live.game.<gid>.state.*` is state-like; coalescing is acceptable.
        if topic.startswith("live.worker.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
            return True
        if topic.startswith("live.game.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
            # New protocol: `live.game.<gid>.moves.diff` must never be coalesced.
            if ".moves." in topic:
                return False
            # State diffs/snapshots and full snapshots are coalescible.
            return True
        if topic.startswith("live.assignment.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
            return True
        if topic.startswith("live.engine.") and (topic.endswith(".diff") or topic.endswith(".snapshot")):
            return True
        return False

    def _compact_queue_for_high_priority(
        self, client: LiveWsClient, *, incoming_topic: str, incoming_message: str
    ) -> bool:
        maxsize = getattr(client.queue, "maxsize", 0) or 0
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
            item_topic = self._extract_envelope_topic(item)
            priority = self._priority_for_topic(item_topic or "", item)
            parsed.append((item, item_topic, priority))

        # 1) Drop low priority backlog to make room for the incoming high-priority message.
        kept = [(msg, t, p) for (msg, t, p) in parsed if p <= 2]
        dropped_low = len(parsed) - len(kept)
        if dropped_low:
            self._record_drop(incoming_topic, "compacted_low_priority", dropped_low)

        # 2) If still too large, coalesce worker diffs/snapshots (keep only latest per topic).
        if len(kept) >= maxsize:
            seen: set[str] = set()
            coalesced_rev: list[tuple[str, str | None, int]] = []
            coalesced_drops = 0
            for msg, t, p in reversed(kept):
                if t and self._is_coalescible_topic(t):
                    if t in seen:
                        coalesced_drops += 1
                        continue
                    seen.add(t)
                coalesced_rev.append((msg, t, p))
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
                (msg, t, p) for (msg, t, p) in kept if t and self._is_strict_moves_topic(t)
            ]
            non_strict: list[tuple[str, str | None, int]] = [
                (msg, t, p) for (msg, t, p) in kept if not (t and self._is_strict_moves_topic(t))
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

        for msg, _t, _p in kept:
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
        client.disconnect_reason = reason
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

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def snapshot_diagnostics(self) -> dict[str, Any]:
        """Return a cheap snapshot of hub state for diagnostics endpoints."""

        topics: dict[str, dict[str, Any]] = {}
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
        return {
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


__all__ = ["LiveWebSocketHub"]
