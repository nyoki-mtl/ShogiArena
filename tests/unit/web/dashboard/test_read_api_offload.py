from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from shogiarena._core.contexts.dashboard.adapters.interface_dependency_services import ReadOnlyArchiveGameQuery
from shogiarena._core.interfaces.dashboard.book import api as book_api
from shogiarena._core.interfaces.dashboard.spsa import api as spsa_api
from shogiarena._core.interfaces.dashboard.tournament import api as tournament_api


class _ReadDelay:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.loop = asyncio.get_running_loop()

    def block(self) -> None:
        self.loop.call_soon_threadsafe(self.started.set)
        assert self.release.wait(timeout=1), "Read blocked the event loop timer"

    async def response(self, handler, request):
        task = asyncio.create_task(handler(request))
        try:
            await asyncio.wait_for(self.started.wait(), timeout=2)
            # A separate timer must run while the real API method is awaiting its read.
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            self.release.set()
        return await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,stage",
    [("spsa", "load"), ("spsa", "build"), ("spsa", "event"), ("tournament", "load"), ("tournament", "build")],
)
@pytest.mark.parametrize("read_only", [False, True])
async def test_game_detail_reads_and_builds_in_one_worker(tmp_path, monkeypatch, kind, stage, read_only) -> None:
    module = spsa_api if kind == "spsa" else tournament_api
    api = object.__new__(module.SpsaAPI if kind == "spsa" else module.TournamentAPI)
    api._db_path = tmp_path / "game.db"
    api._read_only = read_only
    delay = _ReadDelay()
    steps = []
    worker_threads = []
    record = SimpleNamespace(
        metadata=SimpleNamespace(
            attributes={}, black_player="black", white_player="white", start_date=None, end_date=None
        ),
        game_name="g1",
        black_time_control=None,
        white_time_control=None,
        init_position_sfen="startpos",
        moves=[],
        result=None,
    )

    def mark(name):
        steps.append(name)
        worker_threads.append(threading.get_ident())
        if name == stage:
            delay.block()

    def load(db_path, *, game_name, immutable=False):
        assert db_path == api._db_path
        assert game_name == "g1"
        assert immutable is read_only
        mark("load")
        return record

    original_builder = module.build_game_detail_payload

    def build(**kwargs):
        mark("build")
        return original_builder(**kwargs)

    def event(game_id):
        assert game_id == "g1"
        mark("event")
        return {"phase": "training", "update_idx": 3}

    query = SimpleNamespace(load_game_record=load)
    api._game_query = ReadOnlyArchiveGameQuery(query) if read_only and kind == "tournament" else query
    api._update_query_service = SimpleNamespace(get_game_event_snapshot=event)
    monkeypatch.setattr(module, "build_game_detail_payload", build)
    request = make_mocked_request("GET", "/api/game/g1", match_info={"game_id": "g1"})

    response = await delay.response(api.get_game, request)

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["game_id"] == "g1"
    assert payload["black_player"] == "black"
    assert payload["moves"] == []
    assert steps == (["load", "build", "event"] if kind == "spsa" else ["load", "build"])
    assert len(set(worker_threads)) == 1
    assert worker_threads[0] != threading.get_ident()
    if kind == "spsa":
        assert payload["black_name"] == "black"
        assert payload["phase"] == "training"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["spsa", "tournament"])
async def test_game_detail_missing_record_keeps_404(tmp_path, kind) -> None:
    api = object.__new__(spsa_api.SpsaAPI if kind == "spsa" else tournament_api.TournamentAPI)
    api._db_path = tmp_path / "game.db"
    api._read_only = True
    api._game_query = SimpleNamespace(load_game_record=lambda *_args, **_kwargs: None)
    # No event service is needed when no record exists.
    response = await api.get_game(make_mocked_request("GET", "/api/game/missing", match_info={"game_id": "missing"}))
    assert response.status == 404
    assert json.loads(response.text)["code"] == "game_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,builder", [("get_summary", "build_book_tab_payload"), ("get_pairs", "build_book_pair_payload")]
)
async def test_book_reads_leave_event_loop_available(tmp_path, monkeypatch, method, builder) -> None:
    delay = _ReadDelay()
    original = getattr(book_api, builder)

    def delayed(*args, **kwargs):
        delay.block()
        assert kwargs == {"compute_out_of_book_plies": True, "allow_full_validation": True}
        return original(*args, **kwargs)

    monkeypatch.setattr(book_api, builder, delayed)
    query = SimpleNamespace(load_games=lambda *_args, **_kwargs: [])
    api = book_api.BookAPI(db_path=tmp_path / "game.db", game_query=query)
    response = await delay.response(getattr(api, method), make_mocked_request("GET", "/api/book?out_of_book=full"))
    assert response.status == 200


@pytest.mark.asyncio
async def test_match_history_read_leaves_event_loop_available(tmp_path) -> None:
    delay = _ReadDelay()
    api = object.__new__(tournament_api.TournamentAPI)
    api._db_path = tmp_path / "game.db"

    def history(db_path, *, limit, offset):
        assert db_path == api._db_path
        assert (limit, offset) == (7, 2)
        delay.block()
        return {
            "games": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "signature": "empty",
            "source": "db",
            "fetched_at": "2026-11-03T00:00:00",
        }

    api._game_query = SimpleNamespace(build_match_history_raw_payload=history)
    response = await delay.response(
        api.get_match_history, make_mocked_request("GET", "/api/tournament/match-history?limit=7&offset=2")
    )
    assert response.status == 200
    assert json.loads(response.text)["offset"] == 2


@pytest.mark.asyncio
async def test_match_history_invalid_query_does_not_read_db() -> None:
    api = object.__new__(tournament_api.TournamentAPI)
    response = await api.get_match_history(make_mocked_request("GET", "/api/tournament/match-history?limit=0"))
    assert response.status == 400
    assert json.loads(response.text)["code"] == "invalid_query"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_game", "get_match_history"])
async def test_tournament_offloaded_payload_keeps_contract_error_response(tmp_path, monkeypatch, method) -> None:
    api = object.__new__(tournament_api.TournamentAPI)
    api._db_path = tmp_path / "game.db"
    api._game_query = SimpleNamespace(
        load_game_record=lambda *_args, **_kwargs: object(),
        build_match_history_raw_payload=lambda *_args, **_kwargs: {"games": "invalid"},
    )
    monkeypatch.setattr(tournament_api, "build_game_detail_payload", lambda **_kwargs: {"game_id": []})
    response = await getattr(api, method)(make_mocked_request("GET", "/api/game/g1", match_info={"game_id": "g1"}))
    assert response.status == 500
    assert json.loads(response.text)["code"] == "internal_contract_error"
