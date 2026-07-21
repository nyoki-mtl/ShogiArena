"""内蔵定跡(A) の有効判定・解決・起動前バリデーションの単体テスト。Task 0014。"""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.shared.kernel.engine_book import (
    collect_book_preflight_errors,
    is_engine_book_enabled,
    resolve_engine_book_path,
    resolve_yaneuraou_book_fallback_path,
)

_HEADER = "#YANEURAOU-DB2016 1.00\n"
_ENTRY = "sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n7g7f none 0 32 1\n"


def _write_book(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestIsEngineBookEnabled:
    def test_no_book_file_is_disabled(self) -> None:
        assert not is_engine_book_enabled({"USI_OwnBook": "true"})

    def test_no_book_sentinel_is_disabled(self) -> None:
        assert not is_engine_book_enabled({"BookFile": "no_book"})

    def test_explicit_usi_own_book_false_disables(self) -> None:
        assert not is_engine_book_enabled({"BookFile": "user_book1.db", "USI_OwnBook": "false"})
        assert not is_engine_book_enabled({"BookFile": "user_book1.db", "USI_OwnBook": False})

    def test_book_file_without_usi_own_book_is_enabled(self) -> None:
        # YaneuraOu の USI_OwnBook 既定は true。省略時も有効とみなす。
        assert is_engine_book_enabled({"BookFile": "user_book1.db"})

    def test_book_file_with_usi_own_book_true_is_enabled(self) -> None:
        assert is_engine_book_enabled({"BookFile": "user_book1.db", "USI_OwnBook": "true"})


class TestResolveEngineBookPath:
    def test_composite_resolution_with_absolute_dir(self) -> None:
        resolved = resolve_engine_book_path({"BookDir": "/srv/book", "BookFile": "user_book1.db"})
        # Windows では Path 経由で区切りが os.sep になるため正規化して比較する。
        assert resolved is not None
        assert resolved.replace("\\", "/") == "/srv/book/user_book1.db"

    def test_disabled_returns_none(self) -> None:
        assert resolve_engine_book_path({"BookFile": "no_book"}) is None


class TestResolveYaneuraouBookFallbackPath:
    def test_db_uses_adjacent_ybb_when_db_is_missing(self, tmp_path: Path) -> None:
        fallback = _write_book(tmp_path, "user_book1.ybb", "binary-placeholder")
        assert resolve_yaneuraou_book_fallback_path(tmp_path / "user_book1.db") == fallback

    def test_existing_db_wins_over_ybb(self, tmp_path: Path) -> None:
        db = _write_book(tmp_path, "user_book1.db", _HEADER + _ENTRY)
        _write_book(tmp_path, "user_book1.ybb", "binary-placeholder")
        assert resolve_yaneuraou_book_fallback_path(db) == db


class TestCollectBookPreflightErrors:
    def test_disabled_book_yields_no_errors(self) -> None:
        assert collect_book_preflight_errors({"BookFile": "no_book"}, engine_name="e") == []

    def test_missing_absolute_book_is_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.db"
        errors = collect_book_preflight_errors(
            {"BookDir": str(tmp_path), "BookFile": "absent.db"},
            engine_name="e1",
        )
        assert len(errors) == 1
        assert "does not exist" in errors[0]
        assert str(missing) in errors[0]

    def test_valid_sorted_book_yields_no_errors(self, tmp_path: Path) -> None:
        _write_book(tmp_path, "ok.db", _HEADER + _ENTRY)
        errors = collect_book_preflight_errors(
            {"BookDir": str(tmp_path), "BookFile": "ok.db"},
            engine_name="e2",
        )
        assert errors == []

    def test_unsorted_book_is_error(self, tmp_path: Path) -> None:
        book = _write_book(tmp_path, "unsorted.db", _HEADER + _ENTRY + _ENTRY)
        errors = collect_book_preflight_errors(
            {"BookFile": str(book)},
            engine_name="e3",
        )
        assert len(errors) == 1
        assert "not sorted" in errors[0]

    def test_book_without_entries_is_error(self, tmp_path: Path) -> None:
        book = _write_book(tmp_path, "empty.db", _HEADER)
        errors = collect_book_preflight_errors(
            {"BookFile": str(book)},
            engine_name="e4",
        )
        assert len(errors) == 1
        assert "no book entries" in errors[0]

    def test_relative_book_path_missing_is_error(self, tmp_path: Path) -> None:
        # 相対 BookDir + BookFile（自然な YaneuraOu 指定）も working_dir 基準で存在確認する。
        errors = collect_book_preflight_errors(
            {"BookDir": "book", "BookFile": "user_book1.db"},
            engine_name="e5",
            working_dir=tmp_path,
        )
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_relative_book_path_resolves_against_working_dir(self, tmp_path: Path) -> None:
        book_dir = tmp_path / "book"
        book_dir.mkdir()
        _write_book(book_dir, "user_book1.db", _HEADER + _ENTRY)
        errors = collect_book_preflight_errors(
            {"BookDir": "book", "BookFile": "user_book1.db"},
            engine_name="e6",
            working_dir=tmp_path,
        )
        assert errors == []

    def test_missing_db_uses_ybb_fallback(self, tmp_path: Path) -> None:
        book_dir = tmp_path / "book"
        book_dir.mkdir()
        _write_book(book_dir, "user_book1.ybb", "binary-placeholder")
        errors = collect_book_preflight_errors(
            {"BookDir": "book", "BookFile": "user_book1.db"},
            engine_name="e7",
            working_dir=tmp_path,
        )
        assert errors == []
