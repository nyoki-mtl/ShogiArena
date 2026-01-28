"""Tests for SPSA detail view helpers."""

from __future__ import annotations

from aiohttp.test_utils import make_mocked_request

from shogiarena.web.dashboard.backend.spsa import (
    DetailViewConfig,
    apply_detail_view,
    parse_detail_view_config,
)


def _build_detail_payload() -> dict:
    games = [
        {
            "game_id": "g-1",
            "black_player": "A",
            "white_player": "B",
        }
    ]
    ltc_games = [
        {
            "game_id": "ltc-1",
            "black_player": "A",
            "white_player": "B",
        }
    ]
    return {
        "update_idx": 9,
        "engines": {"baseline": "base", "tuned": "tuned"},
        "wdl": {"wins": 1, "losses": 0, "draws": 0},
        "variant_id": "v0009",
        "params": {"X": 1.0},
        "grads": {"X": 0.1},
        "deltas": {"X": 0.01},
        "s_plus": 0.5,
        "s_minus": -0.5,
        "step": 0.2,
        "games": games,
        "games_count": len(games),
        "ltc_games": ltc_games,
        "ltc_games_count": len(ltc_games),
        "payload": {
            "games_count": len(games),
            "ltc_games": ltc_games,
            "raw_events": [{"event": "game_result"}],
            "score_history": {"samples": [1.0, 0.0, -1.0, 2.5]},
        },
        "phase_wdl": {},
        "has_ltc_regression": False,
    }


def test_apply_detail_view_slim_prunes_heavy_sections() -> None:
    detail = _build_detail_payload()
    config = DetailViewConfig(view="slim", includes=frozenset(), window="short")

    result = apply_detail_view(detail, config)

    assert result["games"] == []
    assert result["ltc_games"] == []
    assert "ltc_games" not in result["payload"]
    assert result["games_count"] == 1
    assert result["ltc_games_count"] == 1
    assert result["meta"]["view"] == "slim"
    assert result["payload"]["score_history"]["view"] == "digest"
    assert result["payload"]["score_history"]["window"] == "short"
    assert "variant_games" in result["meta"]["available_includes"]
    assert result["meta"]["loaded_includes"] == []


def test_apply_detail_view_respects_requested_includes() -> None:
    detail = _build_detail_payload()
    config = DetailViewConfig(
        view="slim",
        includes=frozenset({"variant_games", "ltc_games", "raw_payload"}),
        window="short",
    )

    result = apply_detail_view(detail, config)

    assert result["games"][0]["game_id"] == "g-1"
    assert result["ltc_games"][0]["game_id"] == "ltc-1"
    assert result["payload"].get("ltc_games")
    assert set(result["meta"]["loaded_includes"]) >= {"variant_games", "ltc_games", "raw_payload"}


def test_parse_detail_view_config_validates_view() -> None:
    request = make_mocked_request("GET", "/api/spsa/update/9?view=wide")

    try:
        parse_detail_view_config(request)
    except ValueError:
        pass
    else:  # pragma: no cover - ensure failure raises
        raise AssertionError("Expected ValueError for unsupported view")


def test_parse_detail_view_config_supports_multiple_includes() -> None:
    request = make_mocked_request("GET", "/api/spsa/update/9?include=variant_games,score_history&include=ltc_games")
    config = parse_detail_view_config(request)

    assert config.view == "slim"
    assert config.includes == frozenset({"variant_games", "score_history", "ltc_games"})
    assert config.window == "short"


def test_parse_detail_view_config_supports_window_param() -> None:
    request = make_mocked_request("GET", "/api/spsa/update/9?window=long")
    config = parse_detail_view_config(request)

    assert config.window == "long"


def test_parse_detail_view_config_rejects_unknown_window() -> None:
    request = make_mocked_request("GET", "/api/spsa/update/9?window=giant")

    try:
        parse_detail_view_config(request)
    except ValueError:
        return
    raise AssertionError("Expected ValueError for unsupported window")
