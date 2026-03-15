from __future__ import annotations

import json
from types import SimpleNamespace

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
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardInterfaceDependencies,
    configure_dashboard_interface_dependencies,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import configure_dashboard_service_factory
from shogiarena._core.interfaces.composition_root.default_root import _create_snapshot_storage
from shogiarena._core.interfaces.dashboard.spsa.api import SpsaAPI


def _build_detail_payload() -> dict:
    games = [{"game_id": "g-1", "black_player": "A", "white_player": "B"}]
    ltc_games = [{"game_id": "ltc-1", "black_player": "A", "white_player": "B"}]
    return {
        "update_idx": 9,
        "variant_id": "v0009",
        "games": games,
        "games_count": len(games),
        "ltc_games": ltc_games,
        "ltc_games_count": len(ltc_games),
        "payload": {
            "games": games,
            "ltc_games": ltc_games,
            "score_history": {"samples": [1, 0, -1]},
        },
        "phase_wdl": {"plus": {"wins": 1}},
        "wdl": {"wins": 1, "losses": 0, "draws": 0},
    }


def _build_api(tmp_path, detail_payload: dict) -> SpsaAPI:
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
    api = SpsaAPI(
        db_path=tmp_path / "db.sqlite3",
        run_dir=tmp_path,
    )

    def build_update_detail(idx: int) -> dict:
        if idx != detail_payload["update_idx"]:
            raise ValueError("no events")
        return detail_payload

    api._update_query_service = SimpleNamespace(build_update_detail=build_update_detail)  # type: ignore[attr-defined]
    return api


@pytest.mark.asyncio
async def test_get_update_defaults_to_slim_view(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9", match_info={"idx": "9"})

    response = await api.get_update(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["games"] == []
    assert payload["ltc_games"] == []
    assert payload["meta"]["view"] == "slim"
    assert payload["meta"]["window"] == "short"


@pytest.mark.asyncio
async def test_get_update_respects_include_param(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request(
        "GET",
        "/api/spsa/update/9?include=variant_games",
        match_info={"idx": "9"},
    )

    response = await api.get_update(request)
    payload = json.loads(response.text)

    assert payload["games"]
    assert payload["meta"]["loaded_includes"] == ["variant_games"]


@pytest.mark.asyncio
async def test_get_update_rejects_unknown_view(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?view=wide", match_info={"idx": "9"})

    response = await api.get_update(request)

    assert response.status == 400


@pytest.mark.asyncio
async def test_get_update_rejects_unknown_window(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?window=giant", match_info={"idx": "9"})

    response = await api.get_update(request)

    assert response.status == 400


@pytest.mark.asyncio
async def test_get_update_reports_requested_window(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?window=long", match_info={"idx": "9"})

    response = await api.get_update(request)
    payload = json.loads(response.text)

    assert payload["meta"]["window"] == "long"
