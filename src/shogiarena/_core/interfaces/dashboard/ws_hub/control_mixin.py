"""Control-message handling mixin for the dashboard WebSocket hub."""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import ValidationError

from shogiarena._core.interfaces.dashboard.ws_hub.hub_message_models import (
    LiveSnapshotResolver,
    WsClientMessage,
)
from shogiarena._core.interfaces.dashboard.ws_hub.hub_runtime_models import (
    LiveWsClient,
    TopicState,
)
from shogiarena._core.interfaces.dashboard.ws_hub.topic_policy import (
    is_strict_moves_topic,
    should_bootstrap_on_subscribe,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)


class WsHubControlMixin:
    snapshot_resolver: LiveSnapshotResolver | None
    _topics: dict[str, TopicState]
    _global_subscribers: int
    _topic_subscribers: dict[str, int]

    def _build_envelope(self, topic: str, payload: JsonValue) -> str | None:
        raise NotImplementedError

    def _disconnect_overloaded_client(self, client: LiveWsClient, *, topic: str, reason: str) -> None:
        raise NotImplementedError

    def _record_drop(self, topic: str, reason: str, count: int = 1) -> None:
        raise NotImplementedError

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
        message_obj: JsonObject = {str(key): value for key, value in message.items()}

        try:
            parsed_message = WsClientMessage.model_validate(message_obj)
        except ValidationError:
            logger.debug("Ignoring invalid WS control message payload")
            return

        msg_type = str(parsed_message.type or "").lower()
        if msg_type == "request_snapshot":
            topic = parsed_message.topic
            if isinstance(topic, str) and self.snapshot_resolver:
                from_seq = parsed_message.from_seq
                if from_seq is not None and from_seq >= 0:
                    self._replay_from_seq(client, topic, from_seq)
                replies = list(self.snapshot_resolver(topic))
                for reply_topic, payload in replies:
                    if payload is None:
                        continue
                    serialized = self._build_envelope(reply_topic, payload)
                    if serialized is None:
                        continue
                    try:
                        client.queue.put_nowait(serialized)
                    except asyncio.QueueFull:
                        # If snapshot replies cannot be queued, the client is overloaded.
                        # For strict move recovery, disconnect and let the client reconnect cleanly.
                        if is_strict_moves_topic(topic) or is_strict_moves_topic(reply_topic):
                            self._disconnect_overloaded_client(
                                client,
                                topic=reply_topic,
                                reason="snapshot_reply_queue_full_disconnect",
                            )
                            return
                        logger.debug("WS queue full while replying snapshot topic=%s", reply_topic)

        elif msg_type in {"ack", "subscribe", "unsubscribe", "set_worker_filter"}:
            self._handle_control_message(client, msg_type, message_obj, parsed_message)
            return

    def _handle_control_message(
        self,
        client: LiveWsClient,
        msg_type: str,
        message: JsonObject,
        parsed_message: WsClientMessage,
    ) -> None:
        should_include_analysis = parsed_message.should_include_analysis
        if should_include_analysis is not None:
            client.should_include_analysis = should_include_analysis

        if msg_type == "set_worker_filter":
            workers = parsed_message.workers
            if workers is None:
                client.worker_filter = None
                return
            client.worker_filter = set(workers)
            return

        if msg_type == "subscribe":
            if "topics" not in message:
                return
            topics = parsed_message.topics
            if topics is None:
                self._set_client_subscriptions(client, None)
                return
            valid = {str(t) for t in topics if isinstance(t, str) and t}
            self._set_client_subscriptions(client, valid or None)
            # 初回購読時に最新スナップショットを即送出し、ギャップを埋める
            if client.subscriptions and self.snapshot_resolver:
                for topic in client.subscriptions:
                    if not should_bootstrap_on_subscribe(topic):
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
                            if is_strict_moves_topic(topic) or is_strict_moves_topic(reply_topic):
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
            topics = parsed_message.topics
            if topics is None:
                self._set_client_subscriptions(client, set())
                return
            if client.subscriptions is None:
                # if previously "all", convert to empty set then remove
                self._set_client_subscriptions(client, set())
                return
            next_subs = set(client.subscriptions)
            for topic in topics:
                if isinstance(topic, str):
                    next_subs.discard(topic)
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
                logger.debug("Skipping malformed replay envelope for topic=%s", topic)
                continue
            seq_val = data.get("seq")
            if not isinstance(seq_val, int | float):
                logger.debug("Skipping replay envelope with invalid seq for topic=%s: %r", topic, seq_val)
                continue
            if seq_val > from_seq:
                try:
                    client.queue.put_nowait(serialized)
                    replayed += 1
                except asyncio.QueueFull:
                    if is_strict_moves_topic(topic):
                        self._disconnect_overloaded_client(client, topic=topic, reason="replay_queue_full_disconnect")
                        return replayed
                    logger.debug("WS queue full while replaying topic=%s", topic)
                    break
        return replayed


__all__ = ["WsHubControlMixin"]
