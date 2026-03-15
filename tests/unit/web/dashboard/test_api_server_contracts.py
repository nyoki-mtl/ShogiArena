from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from shogiarena._core.contexts.dashboard.adapters.game_repository import (
    build_games_list_raw_payload,
    build_match_history_raw_payload,
    load_game_record,
    load_games_for_dashboard,
)
from shogiarena._core.contexts.dashboard.adapters.instances_persistence import (
    upsert_instance_spec,
)
from shogiarena._core.contexts.dashboard.adapters.interface_dependency_services import (
    DashboardGameQueryAdapter,
    DashboardInstancesAdapter,
    DashboardRuntimeSupportAdapter,
    DashboardSpsaSupportAdapter,
)
from shogiarena._core.contexts.dashboard.adapters.run_state_loader import load_run_state_mapping
from shogiarena._core.contexts.dashboard.adapters.spsa.ltc_service import SpsaLtcService
from shogiarena._core.contexts.dashboard.adapters.spsa.params_service import SpsaParamsService
from shogiarena._core.contexts.dashboard.adapters.spsa.service_factory import SpsaDashboardServicesFactory
from shogiarena._core.contexts.dashboard.application.event_bus import Event
from shogiarena._core.contexts.dashboard.application.events import DashboardEvent, DashboardEventType
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardInterfaceDependencies,
    configure_dashboard_interface_dependencies,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import configure_dashboard_service_factory
from shogiarena._core.interfaces.composition_root.default_root import (
    _create_api_server,
    _create_snapshot_storage,
)
from shogiarena._core.interfaces.dashboard.api_server.server import ArenaAPIServer


def _build_server(tmp_path) -> ArenaAPIServer:
    configure_dashboard_interface_dependencies(
        DashboardInterfaceDependencies(
            game_query=DashboardGameQueryAdapter(
                games_loader=load_games_for_dashboard,
                game_record_loader=load_game_record,
                games_raw_payload_builder=build_games_list_raw_payload,
                match_history_raw_payload_builder=build_match_history_raw_payload,
            ),
            instances=DashboardInstancesAdapter(
                instance_spec_upserter=upsert_instance_spec,
            ),
            runtime_support=DashboardRuntimeSupportAdapter(
                snapshot_storage_factory=_create_snapshot_storage,
                run_state_loader=load_run_state_mapping,
            ),
            spsa_support=DashboardSpsaSupportAdapter(
                ltc_service_factory=SpsaLtcService,
                params_service_factory=SpsaParamsService,
            ),
        )
    )
    configure_dashboard_service_factory(SpsaDashboardServicesFactory())
    return _create_api_server(
        db_path=tmp_path / "dashboard.sqlite3",
        port=8080,
        run_dir=tmp_path,
    )


@pytest.mark.asyncio
async def test_get_summary_returns_default_tournament_contract(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    request = make_mocked_request("GET", "/api/summary?source=tournament")

    response = await api_server.get_summary(request)
    payload = json.loads(response.text)

    assert payload["summarySource"] == "tournament"
    assert payload["is_summary_ready"] is False
    assert payload["mode"] == "tournament"
    assert payload["games"]["completed"] == 0
    assert payload["games"]["total"] == 0
    assert payload["liveView"]["mode"] == "tournament"
    assert payload["liveView"]["progress"]["completed"] == 0
    assert payload["liveView"]["progress"]["total"] == 0


@pytest.mark.asyncio
async def test_get_summary_rehydrates_missing_live_view_from_snapshot(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    api_server._state.set_summary_snapshot(  # noqa: SLF001
        "tournament",
        {
            "summarySource": "tournament",
            "mode": "tournament",
            "games": {"completed": 12, "total": 20, "cancelled": 1},
            "timestamp": "2026-03-11T00:00:00+00:00",
        },
    )
    request = make_mocked_request("GET", "/api/summary?source=tournament")

    response = await api_server.get_summary(request)
    payload = json.loads(response.text)

    assert payload["games"]["completed"] == 12
    assert payload["games"]["total"] == 20
    assert payload["liveView"]["mode"] == "tournament"
    assert payload["liveView"]["progress"]["completed"] == 12
    assert payload["liveView"]["progress"]["total"] == 20


@pytest.mark.asyncio
async def test_get_summary_unknown_source_defaults_to_empty_object(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    request = make_mocked_request("GET", "/api/summary?source=missing")

    response = await api_server.get_summary(request)

    assert json.loads(response.text) == {}


@pytest.mark.asyncio
async def test_get_worker_returns_null_for_unknown_index(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    request = make_mocked_request("GET", "/api/worker/3", match_info={"worker_idx": "3"})

    response = await api_server.get_worker(request)

    assert response.text == "null"


@pytest.mark.asyncio
async def test_get_worker_returns_payload_after_snapshot_set(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    api_server.set_worker_snapshot(
        7,
        {
            "game_id": "game-7",
            "initial_sfen": "startpos",
            "black_name": "black",
            "white_name": "white",
            "moves": [],
            "ki2_moves": [],
            "eval_black": [],
            "eval_white": [],
            "nodes_values": [],
            "depth_values": [],
            "seldepth_values": [],
            "move_times_ms": [],
            "wall_times_ms": [],
            "latency_deltas_ms": [],
            "latency_alerts": [],
            "currentPly": 0,
            "sfen": "startpos",
            "time_control_black": "t3+0",
            "time_control_white": "t3+0",
        },
    )
    request = make_mocked_request("GET", "/api/worker/7", match_info={"worker_idx": "7"})

    response = await api_server.get_worker(request)
    payload = json.loads(response.text)

    assert payload["game_id"] == "game-7"
    assert payload["initial_sfen"] == "startpos"


def test_handle_assignment_stream_publish_routes_payload_to_ws(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    captured: list[tuple[str, dict[str, object], int | None]] = []

    def fake_publish(topic: str, payload: dict[str, object], *, worker_idx: int | None = None) -> None:
        captured.append((topic, payload, worker_idx))

    api_server._publish_ws = fake_publish  # type: ignore[method-assign]
    api_server._handle_assignment_stream_publish(
        Event(
            DashboardEventType.ASSIGNMENT_STREAM_PUBLISH,
            DashboardEvent(
                event_type=DashboardEventType.ASSIGNMENT_STREAM_PUBLISH,
                worker_idx=11,
                payload={
                    "topic": "live.assignment.snapshot",
                    "payload": {"assignments": {"11": "g11"}, "gids": ["g11"], "assignment_rev": 1, "updatedAt": 123},
                },
            ),
        )
    )

    assert captured == [
        (
            "live.assignment.snapshot",
            {"assignments": {"11": "g11"}, "gids": ["g11"], "assignment_rev": 1, "updatedAt": 123},
            11,
        )
    ]


def test_instances_api_does_not_register_legacy_games_route(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    routes = {route.resource.canonical for route in api_server.app.router.routes()}
    assert "/api/instances/{id}/games" not in routes
