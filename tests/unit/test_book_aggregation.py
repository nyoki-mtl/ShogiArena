"""dashboard Book タブ集計ビルダーの単体テスト。Task 0018。"""

from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.book.aggregation import (
    BookGameInput,
    BookSideInput,
    book_key,
    build_book_aggregation,
)


def _book(partial: str = "abc123", basename: str = "user_book1.db", path: str = "/srv/book/user_book1.db") -> dict:
    return {
        "resolved_path": path,
        "fingerprint": {"basename": basename, "partial_sha256": partial, "hash_method": "size_partial_sha256"},
        "options": {"BookFile": basename, "BookMoves": 16},
    }


class TestBookKey:
    def test_prefers_full_then_partial(self) -> None:
        assert book_key({"fingerprint": {"full_sha256": "full", "partial_sha256": "part"}}) == "full"
        assert book_key({"fingerprint": {"partial_sha256": "part"}}) == "part"

    def test_falls_back_to_path_then_none(self) -> None:
        assert book_key({"resolved_path": "/x/y.db"}) == "path:/x/y.db"
        assert book_key(None) is None
        assert book_key({}) is None


class TestBuildBookAggregation:
    def test_overall_and_color_win_rates(self) -> None:
        book = _book()
        # 2 局: black が同じ book を使い 1 勝 1 敗。white は book 無し。
        games = [
            BookGameInput(
                black=BookSideInput("E1", "win", book, out_of_book_ply=5),
                white=BookSideInput("E2", "loss", None),
            ),
            BookGameInput(
                black=BookSideInput("E1", "loss", book, out_of_book_ply=3),
                white=BookSideInput("E2", "win", None),
            ),
        ]
        payload = build_book_aggregation(games)
        assert payload["book_count"] == 1
        entry = payload["books"][0]
        assert entry["overall"] == {"games": 2, "wins": 1, "losses": 1, "draws": 0, "win_rate": 0.5}
        assert entry["by_color"]["black"]["games"] == 2
        assert entry["by_color"]["white"]["games"] == 0
        assert entry["engines"]["E1"]["games"] == 2

    def test_out_of_book_histogram(self) -> None:
        book = _book()
        games = [
            BookGameInput(
                black=BookSideInput("E1", "win", book, out_of_book_ply=5),
                white=BookSideInput("E2", "draw", book, out_of_book_ply=5),
            ),
            BookGameInput(
                black=BookSideInput("E1", "draw", book, out_of_book_ply=8),
                white=BookSideInput("E2", "draw", book, out_of_book_ply=None),
            ),
        ]
        payload = build_book_aggregation(games)
        out = payload["books"][0]["out_of_book"]
        assert out["histogram"] == {"5": 2, "8": 1}
        assert out["samples"] == 3
        assert out["unbounded"] == 1
        assert out["max_ply"] == 8

    def test_distinct_books_grouped_separately(self) -> None:
        book_a = _book(partial="aaa", basename="a.db", path="/srv/a.db")
        book_b = _book(partial="bbb", basename="b.db", path="/srv/b.db")
        games = [
            BookGameInput(
                black=BookSideInput("E1", "win", book_a),
                white=BookSideInput("E2", "loss", book_b),
            ),
        ]
        payload = build_book_aggregation(games)
        assert payload["book_count"] == 2
        keys = {entry["key"] for entry in payload["books"]}
        assert keys == {"aaa", "bbb"}

    def test_no_books_yields_empty(self) -> None:
        games = [
            BookGameInput(
                black=BookSideInput("E1", "win", None),
                white=BookSideInput("E2", "loss", None),
            )
        ]
        payload = build_book_aggregation(games)
        assert payload["book_count"] == 0
        assert payload["books"] == []
