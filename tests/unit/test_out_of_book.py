"""out-of-book 算出（実着手 membership + BookMoves クリップ）の単体テスト。Task 0016 spike。"""

from __future__ import annotations

from pathlib import Path

import rsshogi.book as rs_book
from rsshogi.core import Board

from shogiarena._core.shared.kernel.out_of_book import compute_out_of_book

_HEADER = "#YANEURAOU-DB2016 1.00\n"


def _sfen_after(moves: list[str]) -> str:
    board = Board()
    for mv in moves:
        board.apply_usi(mv)
    return board.to_sfen()


def _build_book(tmp_path: Path, entries: list[tuple[str, str]]) -> rs_book.YaneuraOuBook:
    """(sfen, move) エントリから sorted な YaneuraOu DB を生成して開く。

    rsshogi の正規化順に依存しないよう、診断が ``sorted`` になる行順を探す。
    """

    def write(order: list[tuple[str, str]]) -> Path:
        lines = [_HEADER]
        for sfen, mv in order:
            lines.append(f"sfen {sfen}\n{mv} none 0 32 1\n")
        path = tmp_path / "book.db"
        path.write_text("".join(lines), encoding="utf-8")
        return path

    path = write(entries)
    book = rs_book.YaneuraOuBook.open(str(path))
    if book.diagnostics().kind != "sorted":
        path = write(list(reversed(entries)))
        book = rs_book.YaneuraOuBook.open(str(path))
    assert book.diagnostics().kind == "sorted"
    return book


def test_actual_move_in_book_then_leaves(tmp_path: Path) -> None:
    init = Board().to_sfen()
    after_76 = _sfen_after(["7g7f"])
    book = _build_book(tmp_path, [(init, "7g7f"), (after_76, "3c3d")])

    # 1手目 7g7f は book 一致、2手目も 3c3d は book 一致、3手目は entry 無し -> ply3 で離脱。
    result = compute_out_of_book(["7g7f", "3c3d", "2g2f"], book)
    assert result.out_of_book_ply == 3
    assert result.in_book_plies == 2
    assert result.clipped is False


def test_membership_not_just_position(tmp_path: Path) -> None:
    init = Board().to_sfen()
    book = _build_book(tmp_path, [(init, "7g7f")])

    # 初手局面は book に存在するが、実着手 2g2f は候補手 (7g7f) に含まれない -> ply1 で離脱。
    result = compute_out_of_book(["2g2f"], book)
    assert result.out_of_book_ply == 1
    assert result.in_book_plies == 0


def test_book_moves_limit_clips(tmp_path: Path) -> None:
    init = Board().to_sfen()
    after_76 = _sfen_after(["7g7f"])
    book = _build_book(tmp_path, [(init, "7g7f"), (after_76, "3c3d")])

    # limit=1 なら ply2 以降は in-book とみなさずクリップ。
    result = compute_out_of_book(["7g7f", "3c3d", "2g2f"], book, book_moves_limit=1)
    assert result.out_of_book_ply is None
    assert result.in_book_plies == 1
    assert result.clipped is True


def test_unknown_first_position_is_immediate_out(tmp_path: Path) -> None:
    after_76 = _sfen_after(["7g7f"])
    book = _build_book(tmp_path, [(after_76, "3c3d")])

    # 初手局面が book に無い -> ply1 で out-of-book。
    result = compute_out_of_book(["2g2f"], book)
    assert result.out_of_book_ply == 1
    assert result.in_book_plies == 0
