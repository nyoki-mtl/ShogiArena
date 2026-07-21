"""Out-of-book 観測の算出。Task 0016 spike / 0018 集計の共通ロジック。

定義は **「実着手が指定 book 上の候補手集合に含まれるか」**。局面が book に存在するだけでは
in-book としない。``BookMoves``（YaneuraOu 既定 16）を超える ply は in-book とみなさず上限で
クリップする。本指標は **「book 一致の heuristic upper bound」** であり、エンジンが実際に book
由来で指したことの断定ではない。

信頼源: ``agent-docs/architecture/opening-book-and-openings.md`` §6。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# YaneuraOu の BookMoves 既定値（book.cpp）。
DEFAULT_BOOK_MOVES_LIMIT = 16


@runtime_checkable
class _BookMoveLike(Protocol):
    @property
    def mv(self) -> object: ...


@runtime_checkable
class _BookEntryLike(Protocol):
    @property
    def moves(self) -> Sequence[_BookMoveLike]: ...


@runtime_checkable
class BookLookup(Protocol):
    """``lookup_sfen`` を持つ book（rsshogi ``YaneuraOuBook`` / ``MemoryBook`` 等）。"""

    def lookup_sfen(self, sfen: str) -> _BookEntryLike | None: ...


@dataclass(frozen=True, slots=True)
class OutOfBookResult:
    """1 局の out-of-book 観測結果。

    Attributes:
        out_of_book_ply: 実着手が初めて book 候補手集合から外れた 1-indexed ply。
            上限内で最後まで book 一致した場合は ``None``。
        in_book_plies: 先頭から連続して book 一致した ply 数（``book_moves_limit`` で上限）。
        book_moves_limit: 適用した ``BookMoves`` 上限。
        clipped: out-of-book を見つける前に ``book_moves_limit`` で打ち切ったか。
        evaluated_plies: 実際に判定した ply 数。
    """

    out_of_book_ply: int | None
    in_book_plies: int
    book_moves_limit: int
    clipped: bool
    evaluated_plies: int


def _move_usi_set(entry: _BookEntryLike) -> set[str]:
    result: set[str] = set()
    for book_move in entry.moves:
        mv = book_move.mv
        to_usi = getattr(mv, "to_usi", None)
        if callable(to_usi):
            result.add(str(to_usi()))
    return result


def is_book_candidate(book: BookLookup, sfen: str, move_usi: str) -> bool:
    """Return whether ``move_usi`` is one of the book candidates for ``sfen``."""

    entry = book.lookup_sfen(sfen)
    return entry is not None and move_usi in _move_usi_set(entry)


def compute_out_of_book(
    moves_usi: Sequence[str],
    book: BookLookup,
    *,
    book_moves_limit: int = DEFAULT_BOOK_MOVES_LIMIT,
    start_sfen: str | None = None,
) -> OutOfBookResult:
    """実着手列を指定 book で後付け lookup し、out-of-book 観測を算出する。

    各 ply で着手前の局面 SFEN を book で引き、実着手が候補手集合に含まれれば in-book とみなす。
    ``book_moves_limit`` を超える ply は in-book としない（上限クリップ）。

    Args:
        moves_usi: 実着手の USI 文字列列（1 手目から）。
        book: ``lookup_sfen`` を持つ book。
        book_moves_limit: ``BookMoves`` 上限（既定 16）。
        start_sfen: 開始局面 SFEN。``None`` なら平手初期局面。

    Returns:
        :class:`OutOfBookResult`。
    """

    from rsshogi.core import Board

    board = Board(start_sfen) if start_sfen is not None else Board()
    in_book_plies = 0

    for index, move_usi in enumerate(moves_usi):
        ply = index + 1
        if ply > book_moves_limit:
            return OutOfBookResult(
                out_of_book_ply=None,
                in_book_plies=in_book_plies,
                book_moves_limit=book_moves_limit,
                clipped=True,
                evaluated_plies=ply - 1,
            )
        entry = book.lookup_sfen(board.to_sfen())
        if entry is None or move_usi not in _move_usi_set(entry):
            return OutOfBookResult(
                out_of_book_ply=ply,
                in_book_plies=in_book_plies,
                book_moves_limit=book_moves_limit,
                clipped=False,
                evaluated_plies=ply,
            )
        in_book_plies += 1
        board.apply_usi(move_usi)

    return OutOfBookResult(
        out_of_book_ply=None,
        in_book_plies=in_book_plies,
        book_moves_limit=book_moves_limit,
        clipped=len(moves_usi) >= book_moves_limit,
        evaluated_plies=min(len(moves_usi), book_moves_limit),
    )


__all__ = [
    "DEFAULT_BOOK_MOVES_LIMIT",
    "BookLookup",
    "OutOfBookResult",
    "compute_out_of_book",
    "is_book_candidate",
]
