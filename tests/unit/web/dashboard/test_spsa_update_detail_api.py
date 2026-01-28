from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from shogiarena.web.dashboard.backend.spsa import SPSAAPI


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


def _build_api(tmp_path, detail_payload: dict) -> SPSAAPI:
    api = SPSAAPI(
        db_path=tmp_path / "db.sqlite3",
        run_dir=tmp_path,
        ensure_shogidb=lambda: None,
        shogidb_supplier=lambda: None,
    )

    def build_update_detail(idx: int) -> dict:
        if idx != detail_payload["update_idx"]:
            raise ValueError("no events")
        return detail_payload

    api._data_service = SimpleNamespace(build_update_detail=build_update_detail)  # type: ignore[attr-defined]
    return api


@pytest.mark.asyncio
async def test_get_spsa_update_defaults_to_slim_view(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9", match_info={"idx": "9"})

    response = await api.get_spsa_update(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["games"] == []
    assert payload["ltc_games"] == []
    assert payload["meta"]["view"] == "slim"
    assert payload["meta"]["window"] == "short"


@pytest.mark.asyncio
async def test_get_spsa_update_respects_include_param(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request(
        "GET",
        "/api/spsa/update/9?include=variant_games",
        match_info={"idx": "9"},
    )

    response = await api.get_spsa_update(request)
    payload = json.loads(response.text)

    assert payload["games"]
    assert payload["meta"]["loaded_includes"] == ["variant_games"]


@pytest.mark.asyncio
async def test_get_spsa_update_rejects_unknown_view(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?view=wide", match_info={"idx": "9"})

    response = await api.get_spsa_update(request)

    assert response.status == 400


@pytest.mark.asyncio
async def test_get_spsa_update_rejects_unknown_window(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?window=giant", match_info={"idx": "9"})

    response = await api.get_spsa_update(request)

    assert response.status == 400


@pytest.mark.asyncio
async def test_get_spsa_update_reports_requested_window(tmp_path) -> None:
    api = _build_api(tmp_path, _build_detail_payload())
    request = make_mocked_request("GET", "/api/spsa/update/9?window=long", match_info={"idx": "9"})

    response = await api.get_spsa_update(request)
    payload = json.loads(response.text)

    assert payload["meta"]["window"] == "long"
