"""Variant/LTC game stream endpoint handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse_runtime import prepare_sse_runtime
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize

from .query_models import TargetsStreamQuery, resolve_targets_query

logger = logging.getLogger(__name__)


def resolve_variant_stream_targets(handler: Any, limit: int) -> list[int]:
    """Resolve target update indices for variant stream."""
    updates = handler._update_query_service.load_index_updates()
    if not updates:
        updates = handler._update_query_service.collect_updates_from_events()
    if not updates:
        return []
    updates.sort(key=lambda entry: entry.get("update_idx", 0), reverse=True)
    targets: list[int] = []
    for entry in updates[:limit]:
        idx_value = entry.get("update_idx")
        if isinstance(idx_value, int) and idx_value >= 0:
            targets.append(idx_value)
    return targets


async def sse_variant_games(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming variant games updates."""
    MAX_TARGETS = 100
    try:
        parsed_query = TargetsStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")
    try:
        resolved_query = resolve_targets_query(
            parsed_query,
            default_limit=25,
            max_targets=MAX_TARGETS,
            resolve_dynamic_targets=handler.resolve_variant_stream_targets,
        )
    except ValueError as exc:
        return json_error_response(str(exc), status=400, code="invalid_index")
    targets = resolved_query.targets
    has_dynamic_targets = resolved_query.has_dynamic_targets
    target_limit = resolved_query.target_limit

    if not targets:
        return json_error_response("variant data unavailable", status=404, code="no_updates")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_variant_games",
        stream_key="variant_games",
        event_name="spsa_variant_games",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    poll_interval = parsed_query.poll_interval
    poll_interval = max(0.5, min(poll_interval, 10.0))
    should_send_initial = parsed_query.should_send_initial
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signatures: dict[int, str | None] = dict.fromkeys(targets)

    try:
        while True:
            current_targets = targets
            if has_dynamic_targets:
                refreshed = handler.resolve_variant_stream_targets(target_limit)
                if refreshed:
                    current_targets = refreshed
                    targets = refreshed
                    # prune outdated signatures
                    last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}
            batch: list[JsonObject] = []
            for target_idx in current_targets:
                try:
                    detail = handler._update_query_service.build_update_detail(target_idx)
                except ValueError as exc:
                    logger.debug("Skipping variant games detail for idx=%s: %s", target_idx, exc)
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
                if should_send_initial or signature != previous_signature:
                    variant_payload: JsonObject = {
                        "update_idx": target_idx,
                        "variant_id": detail.get("variant_id"),
                        "games": games,
                        "games_count": detail.get("games_count", len(games)),
                        "phase_wdl": detail.get("phase_wdl"),
                        "wdl": detail.get("wdl"),
                        "is_pending": detail.get("is_pending"),
                    }
                    batch.append(variant_payload)
                    last_signatures[target_idx] = signature

            if should_send_initial or batch:
                await runtime.push_event(
                    {
                        "type": "variant_games_batch",
                        "updates": batch,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                should_send_initial = False

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA variant games SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA variant games SSE response")

    return response


async def sse_ltc_games(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming LTC games updates."""
    MAX_TARGETS = 100
    try:
        parsed_query = TargetsStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")
    try:
        resolved_query = resolve_targets_query(
            parsed_query,
            default_limit=25,
            max_targets=MAX_TARGETS,
            resolve_dynamic_targets=handler.resolve_variant_stream_targets,
        )
    except ValueError as exc:
        return json_error_response(str(exc), status=400, code="invalid_index")
    targets = resolved_query.targets
    has_dynamic_targets = resolved_query.has_dynamic_targets
    target_limit = resolved_query.target_limit

    if not targets:
        return json_error_response("ltc data unavailable", status=404, code="no_updates")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_ltc_games",
        stream_key="ltc_games",
        event_name="spsa_ltc_games",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    poll_interval = parsed_query.poll_interval
    poll_interval = max(0.5, min(poll_interval, 10.0))
    should_send_initial = parsed_query.should_send_initial
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signatures: dict[int, str | None] = dict.fromkeys(targets)

    def normalize_ltc_games(raw_games: object) -> list[Mapping[str, JsonValue]]:
        if not isinstance(raw_games, list):
            return []
        normalized: list[Mapping[str, JsonValue]] = []
        for raw_game in raw_games:
            if not isinstance(raw_game, Mapping):
                continue
            normalized_game: dict[str, JsonValue] = {}
            for key, value in raw_game.items():
                normalized_game[str(key)] = json_serialize(value)
            normalized.append(normalized_game)
        return normalized

    def compute_ltc_wdl(games: Sequence[Mapping[str, JsonValue]]) -> dict[str, int]:
        tuned_wins = 0
        baseline_wins = 0
        draws = 0
        for game in games:
            black = coerce_str(game.get("black_player")) or ""
            white = coerce_str(game.get("white_player")) or ""
            is_tuned_as_black = "tuned" in black and "base" in white
            tuned_as_white = "tuned" in white and "base" in black
            if not is_tuned_as_black and not tuned_as_white:
                continue
            game_result_raw = game.get("game_result")
            result = coerce_game_result(game_result_raw)
            if result is None:
                continue
            if result.is_draw():
                draws += 1
            elif result.is_black_win():
                if is_tuned_as_black:
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

    try:
        while True:
            current_targets = targets
            if has_dynamic_targets:
                refreshed = handler.resolve_variant_stream_targets(target_limit)
                if refreshed:
                    current_targets = refreshed
                    targets = refreshed
                    last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}
            batch: list[JsonObject] = []
            for target_idx in current_targets:
                try:
                    detail = handler._update_query_service.build_update_detail(target_idx)
                except ValueError as exc:
                    logger.debug("Skipping LTC detail for idx=%s: %s", target_idx, exc)
                    continue
                ltc_games = detail.get("ltc_games", [])
                normalized_games = normalize_ltc_games(ltc_games)
                ltc_wdl = compute_ltc_wdl(normalized_games)
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
                if should_send_initial or signature != previous_signature:
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

            if should_send_initial or batch:
                await runtime.push_event(
                    {
                        "type": "ltc_games_batch",
                        "updates": batch,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                should_send_initial = False

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
        logger.debug("SPSA LTC games SSE client disconnected: %s", exc)
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA LTC games SSE response")

    return response
