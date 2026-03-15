"""Task runtime mixin for the dashboard WebSocket hub."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import WSMsgType

from shogiarena._core.interfaces.dashboard.ws_hub.hub_runtime_models import LiveWsClient
from shogiarena._core.shared.kernel.json_types import JsonValue

logger = logging.getLogger(__name__)


class WsHubRuntimeMixin:
    heartbeat_interval: float

    def _build_envelope(self, topic: str, payload: JsonValue) -> str | None:
        raise NotImplementedError

    async def _handle_client_message(self, client: LiveWsClient, raw: str) -> None:
        raise NotImplementedError

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
            except (OSError, RuntimeError) as exc:
                logger.debug("WebSocket close failed: %s", exc)

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


__all__ = ["WsHubRuntimeMixin"]
