"""Book タブ service（結果マッピング・入力構築・out-of-book 配線）の単体テスト。Task 0018。"""

from __future__ import annotations

from pathlib import Path

import rsshogi.book as rs_book
from rsshogi.core import Board
from rsshogi.record import GameResult

from shogiarena._core.contexts.dashboard.application.book.aggregation import build_book_aggregation
from shogiarena._core.contexts.dashboard.application.book.service import (
    BookGameRow,
    build_book_inputs,
    side_outcome,
)

_HEADER = "#YANEURAOU-DB2016 1.00\n"
_ENTRY = "sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n7g7f none 0 32 1\n"


def _book(path: str = "/srv/book/user_book1.db") -> dict:
    return {
        "resolved_path": path,
        "fingerprint": {"basename": "user_book1.db", "partial_sha256": "abc", "hash_method": "size_partial_sha256"},
        "options": {"BookFile": "user_book1.db"},
    }


class TestSideOutcome:
    def test_black_win(self) -> None:
        assert side_outcome(GameResult.BLACK_WIN, "black") == "win"
        assert side_outcome(GameResult.BLACK_WIN, "white") == "loss"

    def test_white_win(self) -> None:
        assert side_outcome(GameResult.WHITE_WIN, "white") == "win"
        assert side_outcome(GameResult.WHITE_WIN, "black") == "loss"

    def test_draw(self) -> None:
        assert side_outcome(GameResult.DRAW_BY_REPETITION, "black") == "draw"
        assert side_outcome(GameResult.DRAW_BY_REPETITION, "white") == "draw"

    def test_indeterminate_is_none(self) -> None:
        assert side_outcome(GameResult.ERROR, "black") is None
        assert side_outcome(GameResult.PAUSED, "white") is None


class TestBuildBookInputs:
    def test_skips_indeterminate_games(self) -> None:
        rows = [BookGameRow("E1", "E2", GameResult.ERROR, _book(), _book())]
        assert build_book_inputs(rows) == []

    def test_maps_outcomes_and_books(self) -> None:
        rows = [BookGameRow("E1", "E2", GameResult.BLACK_WIN, _book(), None)]
        inputs = build_book_inputs(rows)
        assert len(inputs) == 1
        assert inputs[0].black.outcome == "win"
        assert inputs[0].white.outcome == "loss"
        assert inputs[0].black.book is not None
        assert inputs[0].white.book is None
        # out-of-book は既定で算出しないため None。
        assert inputs[0].black.out_of_book_ply is None

    def test_out_of_book_wired_via_injected_opener(self, tmp_path: Path) -> None:
        # 実 book を作り、injected opener で開いて out-of-book を算出。
        book_path = tmp_path / "user_book1.db"
        book_path.write_text(_HEADER + _ENTRY, encoding="utf-8")

        def opener(path: str):
            return rs_book.YaneuraOuBook.open(path)

        book_prov = _book(str(book_path))
        # 初手 7g7f は book 一致、2手目で離脱 -> black の out_of_book_ply=2。
        rows = [
            BookGameRow(
                black_engine="E1",
                white_engine="E2",
                result=GameResult.BLACK_WIN,
                black_book=book_prov,
                white_book=None,
                moves_usi=("7g7f", "3c3d"),
                start_sfen=None,
            )
        ]
        inputs = build_book_inputs(rows, compute_out_of_book_plies=True, book_opener=opener)
        assert inputs[0].black.out_of_book_ply == 2
        assert inputs[0].white.out_of_book_ply is None

    def test_missing_book_file_opener_returns_none(self) -> None:
        def opener(path: str):
            return None

        rows = [
            BookGameRow("E1", "E2", GameResult.BLACK_WIN, _book("/no/such.db"), None, moves_usi=("7g7f",)),
        ]
        inputs = build_book_inputs(rows, compute_out_of_book_plies=True, book_opener=opener)
        assert inputs[0].black.out_of_book_ply is None


def test_end_to_end_aggregation_from_rows(tmp_path: Path) -> None:
    book_path = tmp_path / "user_book1.db"
    book_path.write_text(_HEADER + _ENTRY, encoding="utf-8")
    assert Board().to_sfen()  # sanity: board usable

    book_prov = _book(str(book_path))
    rows = [
        BookGameRow("E1", "E2", GameResult.BLACK_WIN, book_prov, book_prov, moves_usi=("7g7f", "3c3d")),
        BookGameRow("E1", "E2", GameResult.WHITE_WIN, book_prov, book_prov, moves_usi=("7g7f", "8c8d")),
    ]
    inputs = build_book_inputs(rows, compute_out_of_book_plies=True, book_opener=rs_book.YaneuraOuBook.open)
    payload = build_book_aggregation(inputs)
    assert payload["book_count"] == 1
    entry = payload["books"][0]
    assert entry["overall"]["games"] == 4  # 2 局 x 先後
    assert entry["out_of_book"]["histogram"] == {"2": 4}
