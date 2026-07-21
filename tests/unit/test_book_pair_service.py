"""Pair-level book prefix report tests."""

from __future__ import annotations

from pathlib import Path

import rsshogi
from rsshogi.core import Board
from rsshogi.initial_positions import InitialPosition
from rsshogi.record import GameResult

from shogiarena._core.contexts.dashboard.application.book.pairs import build_book_pair_payload
from shogiarena._core.shared.kernel.game_results import GameResult as ArenaGameResult
from shogiarena._core.shared.kernel.participation_records import (
    GameParticipationRecord,
    serialize_participation_records,
)
from shogiarena._core.shared.kernel.schedule_metadata import serialize_schedule_metadata


class _GameQueryStub:
    def __init__(self, records: dict[str, rsshogi.record.Record]) -> None:
        self._records = records

    def load_games(self, db_path: Path, *, game_type: str = "arena") -> list[dict[str, object]]:
        return [
            {
                "game_name": game_name,
                "black_engine": record.metadata.black_player,
                "white_engine": record.metadata.white_player,
                "result": GameResult.BLACK_WIN,
                "initial_sfen": record.init_position_sfen,
            }
            for game_name, record in self._records.items()
        ]

    def load_game_record(self, db_path: Path, *, game_name: str) -> rsshogi.record.Record | None:
        return self._records.get(game_name)


class _FakeMove:
    def __init__(self, move_usi: str) -> None:
        self._move_usi = move_usi

    def to_usi(self) -> str:
        return self._move_usi


class _FakeBookMove:
    def __init__(self, move_usi: str) -> None:
        self.mv = _FakeMove(move_usi)


class _FakeBookEntry:
    def __init__(self, moves: tuple[str, ...]) -> None:
        self.moves = tuple(_FakeBookMove(move) for move in moves)


class _FakeBook:
    def __init__(self, entries: dict[str, tuple[str, ...]]) -> None:
        self._entries = entries

    def lookup_sfen(self, sfen: str) -> _FakeBookEntry | None:
        moves = self._entries.get(sfen)
        return _FakeBookEntry(moves) if moves is not None else None


def _sfen_after(moves: list[str]) -> str:
    board = Board()
    for move in moves:
        board.apply_usi(move)
    return board.to_sfen()


def _participation_metadata(*, black_path: str = "/books/black.db", white_path: str = "/books/white.db") -> str:
    return serialize_participation_records(
        [
            GameParticipationRecord(role="black", engine_name="black", extra={"book": {"resolved_path": black_path}}),
            GameParticipationRecord(role="white", engine_name="white", extra={"book": {"resolved_path": white_path}}),
        ]
    )


def _record(
    *,
    game_name: str,
    order: int,
    moves: list[str],
    initial_sfen: str = InitialPosition.STANDARD.value,
    move_extras: list[dict[str, object] | None] | None = None,
    with_participation: bool = False,
) -> rsshogi.record.Record:
    def move_entry(index: int, move: str) -> dict[str, object]:
        entry: dict[str, object] = {"move": move}
        if move_extras is not None and move_extras[index] is not None:
            entry["engine_info"] = {"extras": move_extras[index]}
        return entry

    attributes = {
        "game_name": game_name,
        "game_type": "arena",
        "updated_date": "2026-01-01T00:00:00",
        "_arena_schedule": serialize_schedule_metadata(
            {
                "schema_version": 1,
                "display_order": order,
                "round_num": order - 1,
                "pair_key": "E1|E2|slot-0",
                "pair_slot": 0,
                "pair_index": 0,
                "matchup_key": "E1|E2",
                "initial_sfen": initial_sfen,
            }
        ),
    }
    if with_participation:
        attributes["_arena_participation"] = _participation_metadata()
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "E1" if order == 1 else "E2",
                "white_player": "E2" if order == 1 else "E1",
                "game_name": game_name,
                "game_type": "arena",
                "updated_date": "2026-01-01T00:00:00",
                "attributes": attributes,
            },
            "init_position_sfen": initial_sfen,
            "moves": [move_entry(index, move) for index, move in enumerate(moves)],
            "result": {"result": ArenaGameResult.BLACK_WIN.name, "ply_count": len(moves)},
        }
    )


def test_build_book_pair_payload_reports_prefix_first_diff(tmp_path: Path) -> None:
    records = {
        "g0001-left": _record(
            game_name="g0001-left",
            order=1,
            moves=["7g7f", "3c3d"],
            move_extras=[{"book_hit": True}, {"book_hit": True}],
        ),
        "g0002-right": _record(
            game_name="g0002-right",
            order=2,
            moves=["7g7f", "8c8d"],
            move_extras=[{"book_hit": True}, {"book_hit": True}],
        ),
    }

    payload = build_book_pair_payload(_GameQueryStub(records), tmp_path / "game.db")  # type: ignore[arg-type]

    assert payload["summary"]["pairs"] == 1
    pair = payload["pairs"][0]
    assert pair["orders"] == [1, 2]
    assert pair["matched_prefix_plies"] == 1
    assert pair["first_diff_ply"] == 2
    assert pair["first_diff_reason"] == "move_mismatch"
    assert pair["prefix_match_rate"] == 0.5
    assert pair["measurement_status"] == "measured"


def test_book_pair_payload_preserves_unknown_book_hit_as_unmeasured(tmp_path: Path) -> None:
    records = {
        "g0001-left": _record(
            game_name="g0001-left",
            order=1,
            moves=["7g7f"],
            move_extras=[{"book_hit": "unknown"}],
        ),
        "g0002-right": _record(
            game_name="g0002-right",
            order=2,
            moves=["7g7f"],
            move_extras=[{"book_hit": "unknown"}],
        ),
    }

    payload = build_book_pair_payload(_GameQueryStub(records), tmp_path / "game.db")  # type: ignore[arg-type]

    pair = payload["pairs"][0]
    assert pair["measurement_status"] == "unmeasured"
    assert pair["left"]["measurement_source"] == "unmeasured"
    assert pair["left"]["book_prefix_length"] == 0


def test_book_pair_summary_excludes_ambiguous_pairs_from_mean(tmp_path: Path) -> None:
    records = {
        "g0001-left": _record(
            game_name="g0001-left",
            order=1,
            moves=["7g7f"],
            move_extras=[{"book_hit": True}],
        ),
        "g0002-right": _record(
            game_name="g0002-right",
            order=2,
            moves=["7g7f"],
            move_extras=[{"book_hit": True}],
        ),
        "g0003-extra": _record(
            game_name="g0003-extra",
            order=3,
            moves=["7g7f"],
            move_extras=[{"book_hit": True}],
        ),
    }

    payload = build_book_pair_payload(_GameQueryStub(records), tmp_path / "game.db")  # type: ignore[arg-type]

    assert payload["summary"]["ambiguous_pairs"] == 1
    assert payload["summary"]["mean_prefix_match_rate"] is None


def test_book_pair_payload_uses_board_turn_for_lookup_book_owner(tmp_path: Path) -> None:
    white_to_move_sfen = _sfen_after(["7g7f"])
    records = {
        "g0001-left": _record(
            game_name="g0001-left",
            order=1,
            initial_sfen=white_to_move_sfen,
            moves=["3c3d"],
            with_participation=True,
        ),
        "g0002-right": _record(
            game_name="g0002-right",
            order=2,
            initial_sfen=white_to_move_sfen,
            moves=["3c3d"],
            with_participation=True,
        ),
    }
    books = {
        "/books/black.db": _FakeBook({white_to_move_sfen: ()}),
        "/books/white.db": _FakeBook({white_to_move_sfen: ("3c3d",)}),
    }

    payload = build_book_pair_payload(
        _GameQueryStub(records),  # type: ignore[arg-type]
        tmp_path / "game.db",
        compute_out_of_book_plies=True,
        book_opener=lambda path: books.get(path),
    )

    pair = payload["pairs"][0]
    assert pair["left"]["measurement_source"] == "book_lookup"
    assert pair["left"]["book_prefix_length"] == 1
    assert pair["measurement_status"] == "measured"


def test_book_pair_payload_marks_mixed_explicit_and_lookup_sources(tmp_path: Path) -> None:
    after_76 = _sfen_after(["7g7f"])
    records = {
        "g0001-left": _record(
            game_name="g0001-left",
            order=1,
            moves=["7g7f", "3c3d"],
            move_extras=[{"book_hit": True}, None],
            with_participation=True,
        ),
        "g0002-right": _record(
            game_name="g0002-right",
            order=2,
            moves=["7g7f", "3c3d"],
            move_extras=[{"book_hit": True}, None],
            with_participation=True,
        ),
    }
    books = {
        "/books/black.db": _FakeBook({}),
        "/books/white.db": _FakeBook({after_76: ("3c3d",)}),
    }

    payload = build_book_pair_payload(
        _GameQueryStub(records),  # type: ignore[arg-type]
        tmp_path / "game.db",
        compute_out_of_book_plies=True,
        book_opener=lambda path: books.get(path),
    )

    pair = payload["pairs"][0]
    assert pair["left"]["measurement_source"] == "mixed"
    assert pair["left"]["book_prefix_length"] == 2
    assert pair["measurement_status"] == "measured"
