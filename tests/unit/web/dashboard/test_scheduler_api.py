from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from shogiarena._core.interfaces.dashboard.scheduler_api import SchedulerAPI
from shogiarena._core.shared.kernel.json_types import JsonObject


class _RunnerStub:
    async def get_schedule_snapshot(self) -> JsonObject:
        return {"schedule": [{"game_id": "runner"}], "revision": 1, "total_games": 1}

    async def request_reschedule(self, *, seed: str | None = None) -> JsonObject:
        return {"seed": seed}

    async def restore_game(self, game_id: str) -> JsonObject:
        return {"game_id": game_id}

    async def cancel_pending_games(self) -> JsonObject:
        return {"cancelled": 0}

    async def cancel_game(self, game_id: str) -> JsonObject:
        return {"game_id": game_id}

    async def set_game_instance(
        self,
        game_id: str,
        *,
        mode: str,
        shared_instance: str | None = None,
        black_instance: str | None = None,
        white_instance: str | None = None,
        should_require_install: bool = False,
    ) -> JsonObject:
        return {
            "game_id": game_id,
            "mode": mode,
            "shared_instance": shared_instance,
            "black_instance": black_instance,
            "white_instance": white_instance,
            "should_require_install": should_require_install,
        }


@pytest.mark.asyncio
async def test_get_schedule_uses_games_snapshot_payload_when_supplier_is_valid() -> None:
    api = SchedulerAPI(
        _RunnerStub(),
        games_snapshot_supplier=lambda: {
            "kind": "bulk",
            "revision": 9,
            "base_revision": 8,
            "rows": [{"game_id": "supplier"}],
            "snapshot_meta": {"total_games": 7, "pending_games": 2},
        },
    )

    response = await api.get_schedule(make_mocked_request("GET", "/api/schedule"))
    assert response.text is not None
    payload = json.loads(response.text)

    assert payload["revision"] == 9
    assert payload["schedule"] == [{"game_id": "supplier"}]
    assert payload["total_games"] == 7
    assert payload["pending_games"] == 2


@pytest.mark.asyncio
async def test_get_schedule_falls_back_to_runner_snapshot_when_supplier_invalid() -> None:
    api = SchedulerAPI(
        _RunnerStub(),
        games_snapshot_supplier=lambda: {
            "kind": "bulk",
            "revision": "invalid",
            "rows": [],
        },
    )

    response = await api.get_schedule(make_mocked_request("GET", "/api/schedule"))
    assert response.text is not None
    payload = json.loads(response.text)

    assert payload["revision"] == 1
    assert payload["schedule"] == [{"game_id": "runner"}]


@pytest.mark.asyncio
async def test_archived_schedule_is_available_without_live_runner() -> None:
    api = SchedulerAPI(
        archived_schedule_supplier=lambda: {
            "revision": 0,
            "schedule": [{"game_id": "archived", "status": "completed"}],
            "total_games": 1,
            "completed_games": 1,
        }
    )
    app = web.Application()
    api.register_routes(app)

    response = await api.get_schedule(make_mocked_request("GET", "/api/schedule"))
    assert response.text is not None
    payload = json.loads(response.text)

    assert payload["schedule"] == [{"game_id": "archived", "status": "completed"}]
    assert {route.method for route in app.router.routes()} == {"GET", "HEAD"}


@pytest.mark.asyncio
async def test_games_snapshot_schedule_is_available_without_live_runner() -> None:
    api = SchedulerAPI(
        games_snapshot_supplier=lambda: {
            "kind": "bulk",
            "revision": 4,
            "base_revision": 3,
            "rows": [{"game_id": "csa-game", "status": "completed"}],
            "snapshot_meta": {"total_games": 1, "completed_games": 1},
        }
    )
    app = web.Application()
    api.register_routes(app)

    response = await api.get_schedule(make_mocked_request("GET", "/api/schedule"))
    payload = json.loads(response.text)

    assert payload["schedule"] == [{"game_id": "csa-game", "status": "completed"}]
    assert {route.method for route in app.router.routes()} == {"GET", "HEAD"}
