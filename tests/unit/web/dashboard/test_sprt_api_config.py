"""Regression tests for SPRT dashboard config coercion and validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.interfaces.dashboard.sprt.api import SprtAPI
from shogiarena._core.shared.kernel.game_results import GameResult


def _build_api(sprt_config: dict, games: list | None = None) -> SprtAPI:
    rows = games or []
    api = object.__new__(SprtAPI)
    api._db_path = Path("unused.db")  # type: ignore[attr-defined]
    api._run_dir = Path(".")  # type: ignore[attr-defined]
    api._mode = "sprt"  # type: ignore[attr-defined]
    api._logger = logging.getLogger("test.sprt")  # type: ignore[attr-defined]
    api._summary_supplier = None  # type: ignore[attr-defined]
    api._game_query = SimpleNamespace(load_games=lambda _path: rows)  # type: ignore[attr-defined]
    run_state = {"config": {"engines": ["tested", "baseline"], "sprt": sprt_config}}
    api._runtime_support = SimpleNamespace(  # type: ignore[attr-defined]
        load_run_state=lambda _run_dir, **_kwargs: run_state,
    )
    return api


def _game(black: str, white: str, result: GameResult, game_name: str) -> dict:
    return {
        "black_engine": black,
        "white_engine": white,
        "result": result,
        "game_name": game_name,
        "initial_sfen": "startpos",
    }


@pytest.mark.asyncio
async def test_invalid_sprt_config_returns_400() -> None:
    # elo0=5 with no elo1 falls back to the default 5.0, making elo1 <= elo0 which the
    # Sprt model rejects. This must surface as a 400, not a 500.
    api = _build_api({"elo0": 5})

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["code"] == "invalid_sprt_config"


@pytest.mark.asyncio
async def test_explicit_zero_elo1_is_preserved() -> None:
    # An explicit elo1=0.0 must not be replaced by the default 5.0 (regression for the
    # `or`-fallback bug that dropped legitimate falsy values).
    api = _build_api({"elo0": -5.0, "elo1": 0.0, "alpha": 0.05, "beta": 0.05})

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["config"]["elo0"] == -5.0
    assert payload["config"]["elo1"] == 0.0


@pytest.mark.asyncio
async def test_pentanomial_replay_pairs_by_round_and_respects_safety_floor() -> None:
    # Two colour-reversed WW pairs are replayed, but a tiny zero-variance sample must not decide.
    games = [
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0001-x"),
        _game("baseline", "tested", GameResult.WHITE_WIN, "g0002-x"),
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0003-x"),
        _game("baseline", "tested", GameResult.WHITE_WIN, "g0004-x"),
    ]
    api = _build_api({"model": "gsprt-pentanomial-v1", "elo0": 0.0, "elo1": 400.0, "alpha": 0.49, "beta": 0.49}, games)

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["config"]["model"] == "gsprt-pentanomial-v1"
    assert payload["status"]["decision"] == "continue"
    assert payload["status"]["pending_pairs"] == 0
    assert payload["status"]["games"] == 4


@pytest.mark.asyncio
async def test_pentanomial_replay_reports_pending_for_unpaired_game() -> None:
    # A single game of a pair leaves one buffered, uncounted half.
    games = [_game("tested", "baseline", GameResult.BLACK_WIN, "g0001-x")]
    api = _build_api({"model": "gsprt-pentanomial-v1", "elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05}, games)

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"]["decision"] == "continue"
    assert payload["status"]["pending_games"] == 1
    assert payload["status"]["pending_pairs"] == 1


@pytest.mark.asyncio
async def test_trinomial_decision_masked_below_min_games() -> None:
    # The runner only stops once games_played >= min_games, so the dashboard must not display a
    # decision before then even if the LLR has crossed a bound. W4/D1/L1 = 6 games < min_games 100.
    games = [
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0001-a"),
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0002-a"),
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0003-a"),
        _game("tested", "baseline", GameResult.BLACK_WIN, "g0004-a"),
        _game("tested", "baseline", GameResult.DRAW_BY_REPETITION, "g0005-a"),
        _game("tested", "baseline", GameResult.WHITE_WIN, "g0006-a"),
    ]
    api = _build_api(
        {"model": "gsprt-trinomial-v1", "elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05, "min_games": 100},
        games,
    )

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"]["games"] == 6
    assert payload["config"]["min_games"] == 100
    assert payload["status"]["decision"] == "continue"  # masked: 6 < 100
    assert all(entry["decision"] == "continue" for entry in payload["timeline"])
