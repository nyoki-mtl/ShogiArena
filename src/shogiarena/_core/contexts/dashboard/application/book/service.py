"""dashboard Book タブのデータ構築サービス。Task 0018。

記録済み game（結果・先後エンジン・participation の book provenance・指し手）から
:func:`build_book_aggregation` 用の入力を作り、Book タブ payload を構築する。

out-of-book ply の算出（rshogi book lookup）は重く book file が必要なため opt-in。
大規模 book（diagnostics incomplete）や unsorted book は lookup の全読み/例外を避けるため
スキップし、out-of-book を「未測定」として扱う。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rshogi.record import GameResult

from shogiarena._core.contexts.dashboard.application.book.aggregation import (
    BookGameInput,
    BookSideInput,
    Color,
    SideOutcome,
    build_book_aggregation,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardGameQueryPort
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.out_of_book import DEFAULT_BOOK_MOVES_LIMIT, BookLookup, compute_out_of_book

logger = logging.getLogger(__name__)

BookOpener = Callable[[str], BookLookup | None]


@dataclass(frozen=True, slots=True)
class BookGameRow:
    """Book 集計に必要な 1 局分の素データ。"""

    black_engine: str
    white_engine: str
    result: GameResult
    black_book: JsonObject | None
    white_book: JsonObject | None
    moves_usi: tuple[str, ...] = ()
    start_sfen: str | None = None


def side_outcome(result: GameResult, color: Color) -> SideOutcome | None:
    """指定 color から見た勝敗を返す。決着不明（中断等）は ``None``。"""

    if result.is_draw():
        return "draw"
    if result.is_black_win():
        return "win" if color == "black" else "loss"
    if result.is_white_win():
        return "win" if color == "white" else "loss"
    return None


def make_default_book_opener(*, allow_full_validation: bool = False) -> BookOpener:
    """既定の book opener を返す。

    `lookup_sfen` は完全なソート検証が済んでいないと例外を投げるため、bounded 検証で
    ``complete is False`` の book（2.4GB 級など）は既定でスキップする。
    ``allow_full_validation=True`` の場合のみ ``validate_full()``（全読み・高コスト）を実行し、
    sorted なら lookup 可能として扱う。これが大規模 book の out-of-book 算出 opt-in 経路。
    """

    def opener(path: str) -> BookLookup | None:
        try:
            from rshogi.book import YaneuraOuBook
        except ImportError:
            return None
        candidate = Path(path)
        if not candidate.is_absolute() or not candidate.is_file():
            return None
        try:
            book = YaneuraOuBook.open(str(candidate))
            diagnostics = book.diagnostics()
            if diagnostics.kind == "sorted" and diagnostics.complete is False:
                if not allow_full_validation:
                    logger.info(
                        "Skipping out-of-book lookup for %s (large/on-the-fly; pass out_of_book=full to validate)",
                        path,
                    )
                    return None
                diagnostics = book.validate_full()
        except ValueError:
            return None
        if diagnostics.kind != "sorted":
            logger.info("Skipping out-of-book lookup for %s (diagnostics kind=%s)", path, diagnostics.kind)
            return None
        return book

    return opener


def _default_book_opener(path: str) -> BookLookup | None:
    return make_default_book_opener()(path)


def _out_of_book_ply(
    book: JsonObject | None,
    moves_usi: Sequence[str],
    start_sfen: str | None,
    *,
    book_moves_limit: int,
    opener: BookOpener,
    cache: dict[str, BookLookup | None],
) -> int | None:
    if book is None or not moves_usi:
        return None
    path = book.get("resolved_path")
    if not isinstance(path, str) or not path:
        return None
    if path not in cache:
        cache[path] = opener(path)
    lookup = cache[path]
    if lookup is None:
        return None
    try:
        result = compute_out_of_book(moves_usi, lookup, book_moves_limit=book_moves_limit, start_sfen=start_sfen)
    except ValueError as exc:
        logger.debug("out-of-book computation failed for %s: %s", path, exc)
        return None
    return result.out_of_book_ply


def build_book_inputs(
    rows: Iterable[BookGameRow],
    *,
    compute_out_of_book_plies: bool = False,
    book_moves_limit: int = DEFAULT_BOOK_MOVES_LIMIT,
    allow_full_validation: bool = False,
    book_opener: BookOpener | None = None,
) -> list[BookGameInput]:
    """:class:`BookGameRow` 群を :class:`BookGameInput` に変換する。

    決着不明（先手勝ち/後手勝ち/引き分けのいずれでもない）局はスキップする。
    ``allow_full_validation`` で大規模 book の ``validate_full`` を許可する（高コスト opt-in）。
    """

    opener = (
        book_opener
        if book_opener is not None
        else make_default_book_opener(allow_full_validation=allow_full_validation)
    )
    cache: dict[str, BookLookup | None] = {}
    inputs: list[BookGameInput] = []

    for row in rows:
        black_outcome = side_outcome(row.result, "black")
        white_outcome = side_outcome(row.result, "white")
        if black_outcome is None or white_outcome is None:
            continue
        oob_black: int | None = None
        oob_white: int | None = None
        if compute_out_of_book_plies:
            oob_black = _out_of_book_ply(
                row.black_book,
                row.moves_usi,
                row.start_sfen,
                book_moves_limit=book_moves_limit,
                opener=opener,
                cache=cache,
            )
            oob_white = _out_of_book_ply(
                row.white_book,
                row.moves_usi,
                row.start_sfen,
                book_moves_limit=book_moves_limit,
                opener=opener,
                cache=cache,
            )
        inputs.append(
            BookGameInput(
                black=BookSideInput(row.black_engine, black_outcome, row.black_book, oob_black),
                white=BookSideInput(row.white_engine, white_outcome, row.white_book, oob_white),
            )
        )
    return inputs


def _book_from_participation(participation: object) -> JsonObject | None:
    extra = getattr(participation, "extra", None)
    if isinstance(extra, Mapping):
        book = extra.get("book")
        if isinstance(book, Mapping):
            return dict(book)
    return None


def build_book_tab_payload(
    game_query: DashboardGameQueryPort,
    db_path: Path,
    *,
    game_type: str = "arena",
    compute_out_of_book_plies: bool = False,
    allow_full_validation: bool = False,
    book_moves_limit: int = DEFAULT_BOOK_MOVES_LIMIT,
) -> JsonObject:
    """記録済み DB から Book タブ payload を構築する。

    各 game の participation metadata から book provenance を取り出し、結果・先後エンジンと
    合わせて集計する。``compute_out_of_book_plies`` 指定時のみ book file を開いて out-of-book を算出。
    ``allow_full_validation`` で大規模（on-the-fly）book の ``validate_full`` を許可する。
    DB アクセスは :class:`DashboardGameQueryPort` 経由で行い、adapter へ直接依存しない。
    """

    from shogiarena._core.shared.kernel.participation_records import extract_participation

    games_meta = game_query.load_games(db_path, game_type=game_type)
    rows: list[BookGameRow] = []
    for meta in games_meta:
        black_engine = meta.get("black_engine")
        white_engine = meta.get("white_engine")
        result = meta.get("result")
        game_name = meta.get("game_name")
        if not isinstance(black_engine, str) or not isinstance(white_engine, str) or not isinstance(result, GameResult):
            continue
        record = game_query.load_game_record(db_path, game_name=game_name) if isinstance(game_name, str) else None
        black_book: JsonObject | None = None
        white_book: JsonObject | None = None
        moves_usi: tuple[str, ...] = ()
        start_sfen: str | None = None
        if record is not None:
            by_role = {participation.role: participation for participation in extract_participation(record)}
            black_book = _book_from_participation(by_role.get("black"))
            white_book = _book_from_participation(by_role.get("white"))
            if compute_out_of_book_plies:
                moves_usi = tuple(move_entry.move.to_usi() for move_entry in record.moves)
                init_sfen = record.init_position_sfen
                start_sfen = None if init_sfen == "startpos" else init_sfen
        rows.append(
            BookGameRow(
                black_engine=black_engine,
                white_engine=white_engine,
                result=result,
                black_book=black_book,
                white_book=white_book,
                moves_usi=moves_usi,
                start_sfen=start_sfen,
            )
        )

    inputs = build_book_inputs(
        rows,
        compute_out_of_book_plies=compute_out_of_book_plies,
        allow_full_validation=allow_full_validation,
        book_moves_limit=book_moves_limit,
    )
    return build_book_aggregation(inputs)


__all__ = [
    "BookGameRow",
    "build_book_inputs",
    "build_book_tab_payload",
    "make_default_book_opener",
    "side_outcome",
]
