"""Regression tests for the SPSA events endpoint payload handling."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from shogiarena._core.interfaces.dashboard.spsa.api import SpsaAPI


def _build_api(event_entries: list[dict]) -> SpsaAPI:
    api = object.__new__(SpsaAPI)
    api._store = SimpleNamespace(load_event_entries=lambda: event_entries)  # type: ignore[attr-defined]
    return api


@pytest.mark.asyncio
async def test_get_events_preserves_original_payload() -> None:
    # The original payload must be returned verbatim, not replaced by a copy of the
    # event metadata (regression for the payload-discard bug).
    entries = [
        {
            "event": "update",
            "ts": 1234,
            "variant_id": "v1",
            "payload": {"gradients": [0.1, -0.2], "step": 3},
        }
    ]
    api = _build_api(entries)
    request = make_mocked_request("GET", "/api/spsa/events?limit=10")

    response = await api.get_events(request)
    body = json.loads(response.text)

    assert response.status == 200
    event = body["events"][0]
    assert event["payload"] == {"gradients": [0.1, -0.2], "step": 3}
    assert event["type"] == "update"
    assert event["timestamp"] == 1234
    # Metadata is preserved at the top level, not folded into payload.
    assert event["variant_id"] == "v1"
    assert "payload" not in event["payload"]


@pytest.mark.asyncio
async def test_get_events_defaults_payload_and_type_when_missing() -> None:
    entries = [{"ts": 5}]
    api = _build_api(entries)
    request = make_mocked_request("GET", "/api/spsa/events?limit=10")

    response = await api.get_events(request)
    body = json.loads(response.text)

    event = body["events"][0]
    assert event["payload"] == {}
    assert event["type"] == "event"
    assert event["timestamp"] == 5


@pytest.mark.asyncio
async def test_get_games_converts_service_failure_to_500() -> None:
    # The listing service propagates DB-layer failures; the API boundary must convert them to a
    # clean 500 rather than leak an unhandled traceback.
    def _raise(**_kwargs: object) -> tuple[list[dict], int]:
        raise RuntimeError("game.db is corrupt")

    api = object.__new__(SpsaAPI)
    api._game_listing_service = SimpleNamespace(list_games=_raise)  # type: ignore[attr-defined]
    request = make_mocked_request("GET", "/api/spsa/games?limit=10&offset=0")

    response = await api.get_games(request)

    assert response.status == 500
    assert json.loads(response.text)["code"] == "games_query_failed"


@pytest.mark.asyncio
async def test_get_games_runs_listing_off_the_event_loop_thread() -> None:
    event_loop_thread = threading.get_ident()
    listing_threads: list[int] = []

    def _list_games(**_kwargs: object) -> tuple[list[dict], int]:
        listing_threads.append(threading.get_ident())
        return [], 0

    api = object.__new__(SpsaAPI)
    api._game_listing_service = SimpleNamespace(list_games=_list_games)  # type: ignore[attr-defined]
    request = make_mocked_request("GET", "/api/spsa/games?limit=10&offset=0")

    response = await api.get_games(request)

    assert response.status == 200
    assert listing_threads
    assert listing_threads[0] != event_loop_thread
