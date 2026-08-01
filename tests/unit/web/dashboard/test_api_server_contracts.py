from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientWebSocketResponse, WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

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


def _build_server(tmp_path, *, read_only: bool = False) -> ArenaAPIServer:
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
        read_only=read_only,
    )


async def _receive_ws_topic(ws: ClientWebSocketResponse, topic: str) -> dict[str, object]:
    while True:
        message = await asyncio.wait_for(ws.receive(), timeout=2.0)
        if message.type == WSMsgType.TEXT:
            envelope = json.loads(message.data)
            if isinstance(envelope, dict) and envelope.get("topic") == topic:
                return {str(key): value for key, value in envelope.items()}
            continue
        raise AssertionError(f"WebSocket closed before topic {topic}: {message.type}")


@pytest.mark.asyncio
async def test_get_summary_returns_default_tournament_contract(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    request = make_mocked_request("GET", "/api/summary?source=tournament")

    response = await api_server.get_summary(request)
    payload = json.loads(response.text)

    assert payload["summary_source"] == "tournament"
    assert payload["is_summary_ready"] is False
    assert payload["mode"] == "tournament"
    assert payload["games"]["completed"] == 0
    assert payload["games"]["total"] == 0
    assert payload["live_view"]["mode"] == "tournament"
    assert payload["live_view"]["progress"]["completed"] == 0
    assert payload["live_view"]["progress"]["total"] == 0


@pytest.mark.asyncio
async def test_get_summary_rehydrates_missing_live_view_from_snapshot(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    api_server._state.set_summary_snapshot(  # noqa: SLF001
        "tournament",
        {
            "summary_source": "tournament",
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
    assert payload["live_view"]["mode"] == "tournament"
    assert payload["live_view"]["progress"]["completed"] == 12
    assert payload["live_view"]["progress"]["total"] == 20


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
                    "payload": {"assignments": {"11": "g11"}, "gids": ["g11"], "assignment_rev": 1, "updated_at": 123},
                },
            ),
        )
    )

    assert captured == [
        (
            "live.assignment.snapshot",
            {"assignments": {"11": "g11"}, "gids": ["g11"], "assignment_rev": 1, "updated_at": 123},
            11,
        )
    ]


def test_instances_api_does_not_register_legacy_games_route(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    routes = {route.resource.canonical for route in api_server.app.router.routes()}
    assert "/api/instances/{id}/games" not in routes


def test_api_server_rejects_non_loopback_bind(tmp_path) -> None:
    with pytest.raises(ValueError, match="explicit loopback address"):
        _create_api_server(
            db_path=tmp_path / "dashboard.sqlite3",
            port=8080,
            run_dir=tmp_path,
            host="0.0.0.0",
        )


@pytest.mark.asyncio
async def test_production_server_preserves_normal_loopback_requests(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    async with TestClient(TestServer(api_server.app)) as client:
        response = await client.get("/api/summary")

    assert response.status == 200


@pytest.mark.asyncio
async def test_production_server_rejects_hostile_host_and_origin(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    async with TestClient(TestServer(api_server.app)) as client:
        hostile_host = await client.get("/api/summary", headers={"Host": "attacker.example"})
        hostile_origin = await client.get(
            "/api/instances/stream",
            headers={"Origin": "https://attacker.example"},
        )

    assert hostile_host.status == 403
    assert hostile_origin.status == 403


@pytest.mark.asyncio
async def test_production_server_requires_same_origin_json_for_mutations(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    async with TestClient(TestServer(api_server.app)) as client:
        origin = str(client.make_url("/")).rstrip("/")
        missing_origin = await client.post(
            "/api/diagnostics/snapshots",
            json={"snapshot": {}},
        )
        hostile_origin = await client.post(
            "/api/diagnostics/snapshots",
            json={"snapshot": {}},
            headers={"Origin": "https://attacker.example"},
        )
        unsafe_content_type = await client.post(
            "/api/diagnostics/snapshots",
            data='{"snapshot": {}}',
            headers={"Content-Type": "text/plain", "Origin": origin},
        )
        accepted = await client.post(
            "/api/diagnostics/snapshots",
            json={"snapshot": {}},
            headers={"Origin": origin},
        )

    assert missing_origin.status == 403
    assert hostile_origin.status == 403
    assert unsafe_content_type.status == 415
    assert accepted.status == 201


@pytest.mark.asyncio
async def test_production_server_rejects_hostile_websocket_origin(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    async with TestClient(TestServer(api_server.app)) as client:
        with pytest.raises(WSServerHandshakeError) as exc_info:
            await client.ws_connect("/ws", origin="https://attacker.example")

    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_production_stop_delivers_latest_terminal_summary_before_websocket_close(tmp_path) -> None:
    api_server = _build_server(tmp_path)
    topic = "live.summary.snapshot.tournament"

    async with TestClient(TestServer(api_server.app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "subscribe", "topics": [topic], "include_analysis": True})
        await _receive_ws_topic(ws, topic)

        api_server.broadcast_summary_update({"games_completed": 7, "games_scheduled": 8})
        seven = await _receive_ws_topic(ws, topic)
        seven_payload = seven["payload"]
        assert isinstance(seven_payload, dict)
        assert seven_payload["games_completed"] == 7

        # The final game update lands inside the normal one-second coalescing window.
        api_server.broadcast_summary_update({"games_completed": 8, "games_scheduled": 8})
        stop_task = asyncio.create_task(api_server.stop())
        try:
            terminal = await _receive_ws_topic(ws, topic)
        finally:
            await stop_task

    terminal_payload = terminal["payload"]
    assert isinstance(terminal_payload, dict)
    assert terminal_payload["games_completed"] == 8
    assert terminal_payload["games_scheduled"] == 8
    assert terminal_payload["tournament_ended"] is True


@pytest.mark.asyncio
async def test_archived_dashboard_server_allows_reads_and_rejects_mutations(tmp_path) -> None:
    api_server = _build_server(tmp_path, read_only=True)
    assert api_server.instance_pool is None
    # 読み取りルートは残す。落とすと archived dashboard の instances タブが 404 になる。
    routes = {route.resource.canonical for route in api_server.app.router.routes()}
    assert "/api/instances" in routes

    async with TestClient(TestServer(api_server.app)) as client:
        assert api_server._instances_health_task is None  # noqa: SLF001
        origin = str(client.make_url("/")).rstrip("/")
        read_response = await client.get("/api/summary")
        instances_response = await client.get("/api/instances")
        instances_payload = await instances_response.json()
        create_instance_response = await client.post(
            "/api/instances",
            json={"name": "should-not-be-created"},
            headers={"Origin": origin},
        )
        create_instance_payload = await create_instance_response.json()
        mutation_response = await client.post(
            "/api/diagnostics/snapshots",
            json={"snapshot": {}},
            headers={"Origin": origin},
        )
        mutation_payload = await mutation_response.json()

    assert read_response.status == 200
    assert instances_response.status == 200
    # 閲覧しただけでローカルインスタンスを生やさない。
    assert instances_payload["instances"] == []
    assert create_instance_response.status == 403
    assert create_instance_payload["code"] == "dashboard_read_only"
    assert mutation_response.status == 403
    assert mutation_payload["code"] == "dashboard_read_only"
    assert not (tmp_path / "diagnostics").exists()


@pytest.mark.asyncio
async def test_spsa_get_does_not_write_artifact_derived_variant_tokens(tmp_path) -> None:
    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    events = [
        {
            "event": "update",
            "update_idx": 1,
            "ts": 1,
            "params": {"ParamA": 1.0},
        },
        {
            "event": "ltc_regression_result",
            "update_idx": 1,
            "ts": 2,
            "status": "accepted",
            "is_accepted": True,
            "tuned_variant_token": "../../escaped",
        },
    ]
    (spsa_dir / "events.jsonl").write_text(
        "".join(f"{json.dumps(event)}\n" for event in events),
        encoding="utf-8",
    )
    api_server = _build_server(tmp_path, read_only=True)

    async with TestClient(TestServer(api_server.app)) as client:
        get_response = await client.get("/api/spsa/updates")
        removed_stream_response = await client.get("/api/spsa/update/detail/stream")

    assert get_response.status == 200
    assert removed_stream_response.status == 404
    assert not (spsa_dir / "accepted-best.json").exists()
    assert not (tmp_path.parent / "escaped.json").exists()


@pytest.mark.asyncio
async def test_live_dashboard_server_still_materializes_a_local_instance(tmp_path) -> None:
    api_server = _build_server(tmp_path)

    async with TestClient(TestServer(api_server.app)) as client:
        instances_response = await client.get("/api/instances")
        instances_payload = await instances_response.json()

    assert instances_response.status == 200
    assert [instance["id"] for instance in instances_payload["instances"]] == ["local"]
