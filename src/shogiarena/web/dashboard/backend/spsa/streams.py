"""SPSA streaming handlers for SSE and WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.client_exceptions import ClientConnectionResetError

from shogiarena.utils.types.types import GameResult
from shogiarena.web.dashboard.backend.http_helpers import json_error_response

from .data_service import SpsaDataService
from .detail_view import apply_detail_view, parse_detail_view_config
from .ltc_service import SpsaLtcService
from .store import SpsaStore

logger = logging.getLogger(__name__)


class SpsaStreams:
    """Streaming handlers for SPSA SSE and WebSocket endpoints.

    Notes:
        - SSE events are wrapped with monotonically increasing `seq` and the SSE `id` field is set.
        - `Last-Event-ID` is accepted and echoed as `resume_from` for diagnostics; no replay is performed.
    """

    def __init__(
        self,
        data_service: SpsaDataService,
        *,
        store: SpsaStore | None = None,
        ltc_service: SpsaLtcService | None = None,
        summary_supplier: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._data_service = data_service
        self._store = store
        self._ltc_service = ltc_service
        self._summary_supplier = summary_supplier
        self._summary_condition: asyncio.Condition = asyncio.Condition()
        self._latest_summary: dict[str, Any] | None = None
        self._detail_metric_name = "spsa:hydration:update-detail"
        self._sse_seq: dict[str, int] = {}

    async def _prepare_sse_response(
        self,
        response: web.StreamResponse,
        request: web.Request,
        *,
        log_context: str,
    ) -> bool:
        """Prepare SSE response, treating client disconnects as normal completion."""
        try:
            await response.prepare(request)
            return True
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ClientConnectionResetError) as exc:
            logger.debug("%s SSE client disconnected before start: %s", log_context, exc)
            return False

    def _progress_payload(self, updates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snapshot = self._data_service.compute_progress_snapshot(updates or [])
        completed = snapshot.get("completed", 0) or 0
        total = snapshot.get("total")
        percent = snapshot.get("percent")
        return {
            "completed": int(completed),
            "total": int(total) if isinstance(total, int | float) else None,
            "percent": float(percent) if isinstance(percent, int | float) else None,
        }

    def _next_sse_seq(self, stream_key: str) -> int:
        current = self._sse_seq.get(stream_key, 0) + 1
        self._sse_seq[stream_key] = current
        return current

    def _wrap_sse_payload(self, stream_key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        seq = self._next_sse_seq(stream_key)
        envelope = dict(payload)
        envelope.setdefault("stream", stream_key)
        envelope["seq"] = seq
        return envelope, seq

    @staticmethod
    def _extract_last_event_id(request: web.Request) -> int | None:
        raw = request.headers.get("Last-Event-ID")
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _inject_resume_from(payload: dict[str, Any], last_event_id: int | None) -> dict[str, Any]:
        if last_event_id is None:
            return payload
        if "resume_from" in payload:
            return payload
        next_payload = dict(payload)
        next_payload["resume_from"] = last_event_id
        return next_payload

    @staticmethod
    def format_sse_event(event_type: str, payload: dict[str, Any], *, event_id: str | None = None) -> bytes:
        """Format SSE event message."""
        parts = []
        if event_id is not None:
            parts.append(f"id: {event_id}")
        parts.append(f"event: {event_type}")
        parts.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
        return ("\n".join(parts) + "\n\n").encode()

    @staticmethod
    def _signature_for_detail(detail: Mapping[str, Any]) -> str:
        """Generate a signature string for change detection.

        Raises:
            TypeError: If detail contains non-serializable values.
        """
        return json.dumps(detail, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _measure_payload_kb(payload: Mapping[str, Any]) -> float:
        """Measure payload size in KB for diagnostics.

        Raises:
            TypeError: If payload contains non-serializable values.
        """
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return len(encoded) / 1024.0

    def _record_detail_metric(self, payload: Mapping[str, Any], *, view: str, include_count: int) -> None:
        size_kb = self._measure_payload_kb(payload)
        if size_kb <= 0.0:
            return
        metric = {
            "metric": self._detail_metric_name,
            "view": view,
            "includeCount": include_count,
            "payloadKb": round(size_kb, 2),
        }
        if view == "slim":
            metric["detailPayloadKbSlim"] = metric["payloadKb"]
        else:
            metric["detailPayloadKbFull"] = metric["payloadKb"]
        logger.debug("[detail-stream] %s", metric)

    def resolve_variant_stream_targets(self, limit: int) -> list[int]:
        """Resolve target update indices for variant stream."""
        updates = self._data_service.load_index_updates()
        if not updates:
            updates = self._data_service.collect_updates_from_events()
        if not updates:
            return []
        updates.sort(key=lambda entry: entry.get("update_idx", 0), reverse=True)
        targets: list[int] = []
        for entry in updates[:limit]:
            idx_value = entry.get("update_idx")
            if isinstance(idx_value, int) and idx_value >= 0:
                targets.append(idx_value)
        return targets

    async def sse_variant_games(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming variant games updates."""
        MAX_TARGETS = 100
        idx_param = request.rel_url.query.get("update_idx")
        limit_param = request.rel_url.query.get("limit")
        dynamic_targets = False
        target_limit = 25
        targets: list[int]

        if idx_param:
            raw_tokens = [token.strip() for token in idx_param.split(",") if token.strip()]
            parsed: list[int] = []
            for token in raw_tokens:
                try:
                    value = int(token)
                except ValueError:
                    return json_error_response("update_idx must be int", status=400, code="invalid_index")
                if value < 0:
                    return json_error_response("update_idx must be non-negative", status=400, code="invalid_index")
                parsed.append(value)
            targets = sorted(set(parsed))[:MAX_TARGETS]
        else:
            dynamic_targets = True
            try:
                target_limit = int(limit_param) if limit_param is not None else 25
            except ValueError:
                target_limit = 25
            target_limit = max(1, min(target_limit, MAX_TARGETS))
            targets = self.resolve_variant_stream_targets(target_limit)

        if not targets:
            return json_error_response("variant data unavailable", status=404, code="no_updates")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_variant_games"):
            return response

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signatures: dict[int, str | None] = dict.fromkeys(targets)

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("variant_games", payload)
            chunk = self.format_sse_event("spsa_variant_games", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                current_targets = targets
                if dynamic_targets:
                    refreshed = self.resolve_variant_stream_targets(target_limit)
                    if refreshed:
                        current_targets = refreshed
                        targets = refreshed
                        # prune outdated signatures
                        last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}
                batch: list[dict[str, Any]] = []
                for target_idx in current_targets:
                    try:
                        detail = self._data_service.build_update_detail(target_idx)
                    except ValueError:
                        continue
                    games = detail.get("games", [])
                    signature = json.dumps(
                        {
                            "games": games,
                            "phase_wdl": detail.get("phase_wdl"),
                            "wdl": detail.get("wdl"),
                            "is_pending": detail.get("is_pending"),
                        },
                        sort_keys=True,
                    )
                    previous_signature = last_signatures.get(target_idx)
                    if send_initial or signature != previous_signature:
                        batch.append(
                            {
                                "update_idx": target_idx,
                                "variant_id": detail.get("variant_id"),
                                "games": games,
                                "games_count": detail.get("games_count", len(games)),
                                "phase_wdl": detail.get("phase_wdl"),
                                "wdl": detail.get("wdl"),
                                "is_pending": detail.get("is_pending"),
                            }
                        )
                        last_signatures[target_idx] = signature

                if send_initial or batch:
                    await push_event(
                        {
                            "type": "variant_games_batch",
                            "updates": batch,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA variant games SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_ltc_games(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming LTC games updates."""
        MAX_TARGETS = 100
        idx_param = request.rel_url.query.get("update_idx")
        limit_param = request.rel_url.query.get("limit")
        dynamic_targets = False
        target_limit = 25
        targets: list[int]

        if idx_param:
            raw_tokens = [token.strip() for token in idx_param.split(",") if token.strip()]
            parsed: list[int] = []
            for token in raw_tokens:
                try:
                    value = int(token)
                except ValueError:
                    return json_error_response("update_idx must be int", status=400, code="invalid_index")
                if value < 0:
                    return json_error_response("update_idx must be non-negative", status=400, code="invalid_index")
                parsed.append(value)
            targets = sorted(set(parsed))[:MAX_TARGETS]
        else:
            dynamic_targets = True
            try:
                target_limit = int(limit_param) if limit_param is not None else 25
            except ValueError:
                target_limit = 25
            target_limit = max(1, min(target_limit, MAX_TARGETS))
            targets = self.resolve_variant_stream_targets(target_limit)

        if not targets:
            return json_error_response("ltc data unavailable", status=404, code="no_updates")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_ltc_games"):
            return response

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signatures: dict[int, str | None] = dict.fromkeys(targets)

        def compute_ltc_wdl(games: list[dict[str, Any]]) -> dict[str, int]:
            tuned_wins = 0
            baseline_wins = 0
            draws = 0
            for game in games:
                black = str(game.get("black_player") or "")
                white = str(game.get("white_player") or "")
                tuned_as_black = "tuned" in black and "base" in white
                tuned_as_white = "tuned" in white and "base" in black
                if not tuned_as_black and not tuned_as_white:
                    continue
                result_code = game.get("result_code")
                if result_code is None:
                    continue
                try:
                    result = GameResult(int(result_code))
                except (TypeError, ValueError):
                    continue
                if result.is_draw():
                    draws += 1
                elif result.is_black_win():
                    if tuned_as_black:
                        tuned_wins += 1
                    else:
                        baseline_wins += 1
                elif result.is_white_win():
                    if tuned_as_white:
                        tuned_wins += 1
                    else:
                        baseline_wins += 1
            return {
                "tuned_wins": tuned_wins,
                "baseline_wins": baseline_wins,
                "draws": draws,
                "total_games": tuned_wins + baseline_wins + draws,
            }

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("ltc_games", payload)
            chunk = self.format_sse_event("spsa_ltc_games", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                current_targets = targets
                if dynamic_targets:
                    refreshed = self.resolve_variant_stream_targets(target_limit)
                    if refreshed:
                        current_targets = refreshed
                        targets = refreshed
                        last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}
                batch: list[dict[str, Any]] = []
                for target_idx in current_targets:
                    try:
                        detail = self._data_service.build_update_detail(target_idx)
                    except ValueError:
                        continue
                    ltc_games = detail.get("ltc_games", [])
                    ltc_wdl = (
                        compute_ltc_wdl(ltc_games)
                        if isinstance(ltc_games, list)
                        else {
                            "tuned_wins": 0,
                            "baseline_wins": 0,
                            "draws": 0,
                            "total_games": 0,
                        }
                    )
                    signature = json.dumps(
                        {
                            "ltc_games": ltc_games,
                            "ltc_games_count": detail.get("ltc_games_count", len(ltc_games)),
                            "ltc_wdl": ltc_wdl,
                            "has_ltc_regression": detail.get("has_ltc_regression"),
                        },
                        sort_keys=True,
                    )
                    previous_signature = last_signatures.get(target_idx)
                    if send_initial or signature != previous_signature:
                        batch.append(
                            {
                                "update_idx": target_idx,
                                "variant_id": detail.get("variant_id"),
                                "ltc_games": ltc_games,
                                "ltc_games_count": detail.get("ltc_games_count", len(ltc_games)),
                                "ltc_wdl": ltc_wdl,
                                "has_ltc_regression": detail.get("has_ltc_regression"),
                            }
                        )
                        last_signatures[target_idx] = signature

                if send_initial or batch:
                    await push_event(
                        {
                            "type": "ltc_games_batch",
                            "updates": batch,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_update_detail(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint that streams filtered SPSA update detail payloads."""

        try:
            detail_view = parse_detail_view_config(request)
        except ValueError:
            return json_error_response("unsupported detail view", status=400, code="invalid_detail_view")

        MAX_TARGETS = 100
        idx_param = request.rel_url.query.get("update_idx")
        limit_param = request.rel_url.query.get("limit")
        dynamic_targets = False
        target_limit = 10

        if idx_param:
            raw_tokens = [token.strip() for token in idx_param.split(",") if token.strip()]
            parsed: list[int] = []
            for token in raw_tokens:
                try:
                    value = int(token)
                except ValueError:
                    return json_error_response("update_idx must be int", status=400, code="invalid_index")
                if value < 0:
                    return json_error_response("update_idx must be non-negative", status=400, code="invalid_index")
                parsed.append(value)
            targets = sorted(set(parsed))[:MAX_TARGETS]
        else:
            dynamic_targets = True
            try:
                target_limit = int(limit_param) if limit_param is not None else 10
            except ValueError:
                target_limit = 10
            target_limit = max(1, min(target_limit, MAX_TARGETS))
            targets = self.resolve_variant_stream_targets(target_limit)

        if not targets:
            return json_error_response("update detail unavailable", status=404, code="no_updates")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_update_detail"):
            return response

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signatures: dict[int, str | None] = dict.fromkeys(targets)
        include_count = len(detail_view.includes)

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("update_detail", payload)
            chunk = self.format_sse_event("spsa_update_detail", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                current_targets = targets
                if dynamic_targets:
                    refreshed = self.resolve_variant_stream_targets(target_limit)
                    if refreshed:
                        current_targets = refreshed
                        targets = refreshed
                        last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}

                batch: list[dict[str, Any]] = []
                for target_idx in current_targets:
                    try:
                        detail = self._data_service.build_update_detail(target_idx)
                    except ValueError:
                        continue
                    filtered = apply_detail_view(detail, detail_view)
                    signature = self._signature_for_detail(filtered)
                    if send_initial or signature != last_signatures.get(target_idx):
                        batch.append(filtered)
                        last_signatures[target_idx] = signature
                        self._record_detail_metric(
                            filtered,
                            view=detail_view.view,
                            include_count=include_count,
                        )

                if send_initial or batch:
                    await push_event(
                        {
                            "type": "detail_batch",
                            "details": batch,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA detail SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_updates(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming SPSA updates."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_updates"):
            return response

        last_event_id = self._extract_last_event_id(request)
        send_initial = request.query.get("send_initial", "true").lower() != "false"
        poll_interval = float(request.query.get("poll_interval", 2.0))
        poll_interval = max(0.5, min(poll_interval, 10.0))
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signatures: dict[int, str] = {}

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("updates", payload)
            chunk = self.format_sse_event("spsa_updates", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        def signature(entry: Mapping[str, Any]) -> str:
            payload = {
                "pending": entry.get("pending"),
                "ended_at": entry.get("ended_at"),
                "delta_norm": entry.get("delta_norm"),
                "wins": entry.get("wins"),
                "losses": entry.get("losses"),
                "draws": entry.get("draws"),
                "phase_wdl": entry.get("phase_wdl"),
                "ltc_regression": entry.get("ltc_regression"),
                "has_ltc_regression": entry.get("has_ltc_regression"),
            }
            return json.dumps(payload, sort_keys=True, ensure_ascii=False)

        try:
            if send_initial:
                updates = self._data_service.load_index_updates()
                if updates:
                    limited = updates[-20:] if updates else []
                    for entry in updates:
                        idx_value = entry.get("update_idx")
                        if isinstance(idx_value, int):
                            last_signatures[idx_value] = signature(entry)
                    payload = {
                        "type": "initial",
                        "updates": limited,
                        "total": len(updates),
                        "timestamp": int(time.time() * 1000),
                        "progress": self._progress_payload(updates),
                    }
                    await push_event(payload)

            while True:
                updates = self._data_service.load_index_updates()
                if updates:
                    changed: list[dict[str, Any]] = []
                    for entry in updates:
                        idx_value = entry.get("update_idx")
                        if not isinstance(idx_value, int):
                            continue
                        current_sig = signature(entry)
                        if last_signatures.get(idx_value) != current_sig:
                            last_signatures[idx_value] = current_sig
                            changed.append(entry)
                    if changed:
                        payload = {
                            "type": "update",
                            "updates": changed,
                            "timestamp": int(time.time() * 1000),
                            "progress": self._progress_payload(updates),
                        }
                        await push_event(payload)

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA updates SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def websocket_updates(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint for SPSA updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        last_update_idx = int(request.query.get("last_update_idx", -1))

        if request.query.get("send_initial", "true").lower() == "true":
            updates = self._data_service.load_index_updates()
            if updates:
                new_updates = [entry for entry in updates if entry.get("update_idx", 0) > last_update_idx]
                await ws.send_str(
                    json.dumps(
                        {
                            "type": "initial",
                            "updates": new_updates[-20:],
                            "total": len(updates),
                        }
                    )
                )

        last_check = 0.0
        check_interval = 2.0

        async for message in ws:
            if message.type == WSMsgType.TEXT:
                payload = json.loads(message.data)
                if payload.get("type") == "ping":
                    await ws.send_str(json.dumps({"type": "pong"}))
                elif payload.get("type") == "subscribe_updates":
                    last_update_idx = payload.get("last_update_idx", last_update_idx)
            elif message.type == WSMsgType.ERROR:
                logger.warning("WebSocket error: %s", ws.exception())
                break

            current_time = time.time()
            if current_time - last_check > check_interval:
                last_check = current_time
                updates = self._data_service.load_index_updates()
                if updates:
                    new_updates = [entry for entry in updates if entry.get("update_idx", 0) > last_update_idx]
                    if new_updates:
                        await ws.send_str(
                            json.dumps(
                                {
                                    "type": "update",
                                    "updates": new_updates,
                                    "timestamp": int(time.time() * 1000),
                                }
                            )
                        )
                        last_update_idx = max(entry.get("update_idx", 0) for entry in new_updates)

        return ws

    async def sse_correlation(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming correlation analysis snapshots."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_correlation"):
            return response

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("correlation", payload)
            chunk = self.format_sse_event("spsa_correlation", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                updates = self._data_service.load_index_updates()
                if len(updates) < 2:
                    updates = self._data_service.collect_updates_from_events()

                result = self._data_service.compute_correlation_analysis(updates)
                signature = json.dumps(result, sort_keys=True, ensure_ascii=False)

                if send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "correlation_update",
                            "data": result,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA correlation SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_convergence(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming convergence analysis."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_convergence"):
            return response

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        try:
            ltc_limit = int(request.rel_url.query.get("ltc_limit", 200))
        except (TypeError, ValueError):
            ltc_limit = 200
        ltc_limit = max(1, min(ltc_limit, 500))
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("convergence", payload)
            chunk = self.format_sse_event("spsa_convergence", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                updates = self._data_service.load_index_updates()
                if not updates:
                    updates = self._data_service.collect_updates_from_events()

                result = self._data_service.compute_convergence_analysis(updates)
                ltc_signature: dict[str, Any] | None = None
                if self._store is not None and self._ltc_service is not None:
                    raw_results = self._store.load_ltc_results()
                    enriched_results = self._ltc_service.enrich_ltc_results(raw_results)
                    total = len(enriched_results)
                    subset = enriched_results[-ltc_limit:] if ltc_limit < total else enriched_results
                    subset_out = list(reversed(subset))
                    summary = self._ltc_service.compute_ltc_summary(enriched_results=enriched_results)
                    result["ltc_results"] = {
                        "total": total,
                        "results": subset_out,
                        "summary": summary,
                    }
                    summary_status = summary.get("status") if isinstance(summary, dict) else None
                    latest = summary.get("latest") if isinstance(summary, dict) else None
                    latest_update = latest.get("update_idx") if isinstance(latest, dict) else None
                    ltc_signature = {
                        "total": total,
                        "status": summary_status,
                        "latest_update": latest_update,
                    }

                # Build signature from key fields for change detection
                mobility_signature: dict[str, Any] | None = None
                mobility_series = result.get("mobility_series")
                if isinstance(mobility_series, dict):
                    gain_series = mobility_series.get("gain_ak")
                    variant_indices = mobility_series.get("variant_indices")
                    if isinstance(gain_series, list) and isinstance(variant_indices, list):
                        last_gain = None
                        for value in reversed(gain_series):
                            if isinstance(value, int | float):
                                last_gain = value
                                break
                        last_variant = None
                        for value in reversed(variant_indices):
                            if isinstance(value, int | float):
                                last_variant = int(value)
                                break
                        mobility_signature = {
                            "count": len(gain_series),
                            "last_gain_ak": last_gain,
                            "last_variant": last_variant,
                        }
                signature_parts = {
                    "delta_norm_history": result.get("delta_norm_history"),
                    "delta_mean_vector_norm_history": result.get("delta_mean_vector_norm_history"),
                    "available_updates": result.get("available_updates"),
                    "pending_updates": result.get("pending_updates"),
                    "convergence_metrics": result.get("convergence_metrics"),
                    "prediction": result.get("prediction"),
                    "ltc_signature": ltc_signature,
                    "mobility_signature": mobility_signature,
                }
                signature = json.dumps(signature_parts, sort_keys=True)

                if send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "convergence_update",
                            "data": result,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA convergence SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    def publish_summary_snapshot(self, payload: Mapping[str, Any]) -> None:
        snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        if not isinstance(snapshot, dict):
            logger.warning("SPSA summary payload is not a dict: %s", type(snapshot).__name__)
            return
        self._latest_summary = snapshot

        async def _notify() -> None:
            async with self._summary_condition:
                self._summary_condition.notify_all()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop yet; snapshot will be served on next poll/connection
            return
        loop.create_task(_notify())

    async def sse_summary(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming SPSA summary snapshots."""
        if self._summary_supplier is None and self._latest_summary is None:
            return json_error_response("spsa summary unavailable", status=503, code="summary_unavailable")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_summary"):
            return response

        last_event_id = self._extract_last_event_id(request)
        update_timeout = float(request.rel_url.query.get("poll_interval", 15.0) or 15.0)
        update_timeout = max(0.5, min(update_timeout, 60.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        last_signature: str | None = None

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("summary", payload)
            chunk = self.format_sse_event("spsa_summary", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        def resolve_snapshot() -> dict[str, Any] | None:
            if self._latest_summary is not None:
                return dict(self._latest_summary)
            if self._summary_supplier is None:
                return None
            try:
                snapshot = self._summary_supplier()
            except Exception:
                logger.exception("Failed to compute SPSA summary snapshot")
                return None
            if snapshot is not None:
                self._latest_summary = dict(snapshot)
            return snapshot

        try:
            if send_initial:
                initial = resolve_snapshot()
                if initial:
                    last_signature = json.dumps(initial, sort_keys=True)
                    await push_event(
                        {
                            "type": "summary",
                            "data": initial,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                send_initial = False

            while True:
                snapshot: dict[str, Any] | None = None
                try:
                    async with self._summary_condition:
                        await asyncio.wait_for(self._summary_condition.wait(), timeout=update_timeout)
                    if self._latest_summary is not None:
                        snapshot = dict(self._latest_summary)
                except asyncio.TimeoutError:
                    snapshot = None

                if snapshot:
                    signature = json.dumps(snapshot, sort_keys=True)
                    if signature != last_signature:
                        await push_event(
                            {
                                "type": "summary",
                                "data": snapshot,
                                "timestamp": int(time.time() * 1000),
                            }
                        )
                        last_signature = signature
                    continue

                await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA summary SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_ltc_results(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming LTC regression results."""
        if self._store is None or self._ltc_service is None:
            return json_error_response("ltc regression unavailable", status=503, code="ltc_unavailable")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_ltc_results"):
            return response

        try:
            limit = int(request.rel_url.query.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 5.0) or 5.0)
        poll_interval = max(1.0, min(poll_interval, 30.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("ltc_results", payload)
            chunk = self.format_sse_event("spsa_ltc_results", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                try:
                    raw_results = self._store.load_ltc_results()
                except OSError:
                    raw_results = []

                enriched_results = self._ltc_service.enrich_ltc_results(raw_results)
                total = len(enriched_results)
                subset = enriched_results[-limit:] if limit < total else enriched_results
                subset_out = list(reversed(subset))
                summary = self._ltc_service.compute_ltc_summary(enriched_results=enriched_results)

                data_payload = {
                    "total": total,
                    "results": subset_out,
                    "summary": summary,
                }
                signature = json.dumps(data_payload, sort_keys=True)

                if send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "ltc_results",
                            "data": data_payload,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("SPSA LTC results SSE client disconnected")
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response

    async def sse_ltc_progress(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for streaming LTC progress updates."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if not await self._prepare_sse_response(response, request, log_context="spsa_ltc_progress"):
            return response

        last_event_id = self._extract_last_event_id(request)
        try:
            poll_interval = float(request.rel_url.query.get("poll_interval", 2.0) or 2.0)
        except (TypeError, ValueError):
            poll_interval = 2.0
        poll_interval = max(0.5, min(poll_interval, 10.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signatures: dict[int, str] = {}

        def ltc_signature(entry: Mapping[str, Any]) -> str:
            payload = {
                "ltc_regression": entry.get("ltc_regression"),
                "has_ltc_regression": entry.get("has_ltc_regression"),
                "ltc_rejected": entry.get("ltc_rejected"),
                "ltc_reverted_to": entry.get("ltc_reverted_to"),
            }
            return json.dumps(payload, sort_keys=True, ensure_ascii=False)

        def extract_ltc_update(entry: Mapping[str, Any]) -> dict[str, Any] | None:
            idx_value = entry.get("update_idx")
            if not isinstance(idx_value, int):
                return None
            return {
                "update_idx": idx_value,
                "ltc_regression": entry.get("ltc_regression"),
                "has_ltc_regression": entry.get("has_ltc_regression"),
                "ltc_rejected": entry.get("ltc_rejected"),
                "ltc_reverted_to": entry.get("ltc_reverted_to"),
            }

        async def push_event(payload: dict[str, Any]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            wrapped, seq = self._wrap_sse_payload("ltc_progress", payload)
            chunk = self.format_sse_event("spsa_ltc_progress", wrapped, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            if send_initial:
                updates = self._data_service.load_index_updates()
                initial_updates: list[dict[str, Any]] = []
                for entry in updates:
                    idx_value = entry.get("update_idx")
                    if isinstance(idx_value, int):
                        last_signatures[idx_value] = ltc_signature(entry)
                        if entry.get("has_ltc_regression") or entry.get("ltc_regression") or entry.get("ltc_rejected"):
                            ltc_entry = extract_ltc_update(entry)
                            if ltc_entry:
                                initial_updates.append(ltc_entry)
                if initial_updates:
                    await push_event(
                        {
                            "type": "ltc_progress",
                            "updates": initial_updates,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    send_initial = False

            while True:
                updates = self._data_service.load_index_updates()
                if updates:
                    changed: list[dict[str, Any]] = []
                    for entry in updates:
                        idx_value = entry.get("update_idx")
                        if not isinstance(idx_value, int):
                            continue
                        signature = ltc_signature(entry)
                        if signature != last_signatures.get(idx_value):
                            last_signatures[idx_value] = signature
                            if (
                                entry.get("has_ltc_regression")
                                or entry.get("ltc_regression")
                                or entry.get("ltc_rejected")
                            ):
                                ltc_entry = extract_ltc_update(entry)
                                if ltc_entry:
                                    changed.append(ltc_entry)
                    if changed:
                        await push_event(
                            {
                                "type": "ltc_progress",
                                "updates": changed,
                                "timestamp": int(time.time() * 1000),
                            }
                        )

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        return response
