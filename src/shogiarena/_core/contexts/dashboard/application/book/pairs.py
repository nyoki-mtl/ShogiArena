"""Pair-level opening book prefix report builder."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import rsshogi.record
from rsshogi.core import Board
from rsshogi.record import GameResult
from rsshogi.types import Color

from shogiarena._core.contexts.dashboard.application.book.service import (
    BookOpener,
    make_default_book_opener,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardGameQueryPort
from shogiarena._core.shared.kernel.book_prefix import (
    BookPrefix,
    BookPrefixMeasurementSource,
    compare_book_prefixes,
    derive_book_prefix,
)
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.out_of_book import DEFAULT_BOOK_MOVES_LIMIT, BookLookup, is_book_candidate
from shogiarena._core.shared.kernel.participation_records import extract_participation
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_bool, coerce_str
from shogiarena._core.shared.kernel.schedule_metadata import extract_schedule_metadata

logger = logging.getLogger(__name__)

_ROUND_RE = re.compile(r"^g(?P<round>\d+)-")


@dataclass(frozen=True, slots=True)
class _PairGame:
    game_id: str
    order: int
    black: str
    white: str
    initial_sfen: str | None
    matchup_key: str
    pair_slot: int
    pair_key: str
    prefix: BookPrefix

    def side_payload(self) -> JsonObject:
        payload = self.prefix.to_payload()
        payload.update(
            {
                "game_id": self.game_id,
                "order": self.order,
                "black": self.black,
                "white": self.white,
            }
        )
        return payload


def _book_from_participation(participation: object) -> JsonObject | None:
    extra = getattr(participation, "extra", None)
    if isinstance(extra, Mapping):
        book = extra.get("book")
        if isinstance(book, Mapping):
            return coerce_json_object_or_none(book)
    return None


def _round_num_from_game_id(game_id: str) -> int | None:
    match = _ROUND_RE.match(game_id)
    if match is None:
        return None
    value = coerce_int(match.group("round"))
    if value is None:
        return None
    return max(0, value - 1)


def _matchup_key(black: str, white: str) -> str:
    return "|".join(sorted((black, white)))


def _metadata_value_int(metadata: JsonObject | None, key: str) -> int | None:
    if metadata is None:
        return None
    return coerce_int(metadata.get(key))


def _metadata_value_str(metadata: JsonObject | None, key: str) -> str | None:
    if metadata is None:
        return None
    return coerce_str(metadata.get(key))


def _move_book_hit(move_record: rsshogi.record.MoveEntry) -> bool | None:
    engine_info = move_record.engine_info
    extras = engine_info.extras if engine_info is not None else None
    if not isinstance(extras, Mapping):
        return None
    if "book_hit" in extras:
        return coerce_optional_bool(extras.get("book_hit"))
    if coerce_str(extras.get("move_source")) == "book":
        return True
    return None


def _lookup_for_book(
    book: JsonObject | None,
    *,
    opener: BookOpener,
    cache: dict[str, BookLookup | None],
) -> BookLookup | None:
    if book is None:
        return None
    path = book.get("resolved_path")
    if not isinstance(path, str) or not path:
        return None
    if path not in cache:
        cache[path] = opener(path)
    return cache[path]


def _lookup_hits(
    *,
    moves_usi: Sequence[str],
    start_sfen: str | None,
    black_book: JsonObject | None,
    white_book: JsonObject | None,
    opener: BookOpener,
    cache: dict[str, BookLookup | None],
    book_moves_limit: int,
) -> list[bool | None]:
    board = Board(start_sfen) if start_sfen is not None and start_sfen != "startpos" else Board()
    hits: list[bool | None] = []
    for index, move_usi in enumerate(moves_usi):
        ply = index + 1
        lookup = None
        if ply <= book_moves_limit:
            book = black_book if board.turn == Color.BLACK else white_book
            lookup = _lookup_for_book(book, opener=opener, cache=cache)
        if lookup is None:
            hits.append(None)
        else:
            try:
                hits.append(is_book_candidate(lookup, board.to_sfen(), move_usi))
            except ValueError as exc:
                logger.debug("book prefix lookup failed at ply %s: %s", ply, exc)
                hits.append(None)
        try:
            board.apply_usi(move_usi)
        except ValueError as exc:
            logger.debug("book prefix board replay failed at ply %s: %s", ply, exc)
            remaining = len(moves_usi) - len(hits)
            hits.extend([None] * remaining)
            break
    return hits


def _combined_hits(
    *,
    explicit_hits: Sequence[bool | None],
    lookup_hits: Sequence[bool | None] | None,
) -> tuple[list[bool | None], BookPrefixMeasurementSource]:
    if lookup_hits is None:
        source = "db_book_hit" if any(hit is not None for hit in explicit_hits) else "unmeasured"
        return list(explicit_hits), source
    combined: list[bool | None] = []
    used_explicit = False
    used_lookup = False
    for explicit, looked_up in zip(explicit_hits, lookup_hits, strict=False):
        if explicit is not None:
            combined.append(explicit)
            used_explicit = True
        else:
            combined.append(looked_up)
            used_lookup = used_lookup or looked_up is not None
    if used_explicit and used_lookup:
        return combined, "mixed"
    if used_explicit:
        return combined, "db_book_hit"
    if used_lookup:
        return combined, "book_lookup"
    return combined, "unmeasured"


def _build_pair_game(
    *,
    game_id: str,
    order: int,
    black: str,
    white: str,
    initial_sfen: str | None,
    record: rsshogi.record.Record,
    should_compute_lookup: bool,
    opener: BookOpener,
    cache: dict[str, BookLookup | None],
    book_moves_limit: int,
) -> _PairGame:
    schedule = extract_schedule_metadata(record)
    round_num = _metadata_value_int(schedule, "round_num")
    if round_num is None:
        round_num_from_id = _round_num_from_game_id(game_id)
        round_num = 0 if round_num_from_id is None else round_num_from_id
    pair_slot = _metadata_value_int(schedule, "pair_slot")
    if pair_slot is None:
        pair_slot = round_num // 2
    matchup_key = _metadata_value_str(schedule, "matchup_key") or _matchup_key(black, white)
    pair_key = _metadata_value_str(schedule, "pair_key") or f"{matchup_key}|slot-{pair_slot}"
    display_order = _metadata_value_int(schedule, "display_order")
    if display_order is None:
        display_order = order

    moves_usi = tuple(move_entry.move.to_usi() for move_entry in record.moves)
    explicit_hits = [_move_book_hit(move_entry) for move_entry in record.moves]
    lookup_values: list[bool | None] | None = None
    if should_compute_lookup:
        by_role = {participation.role: participation for participation in extract_participation(record)}
        black_book = _book_from_participation(by_role.get("black"))
        white_book = _book_from_participation(by_role.get("white"))
        lookup_values = _lookup_hits(
            moves_usi=moves_usi,
            start_sfen=initial_sfen,
            black_book=black_book,
            white_book=white_book,
            opener=opener,
            cache=cache,
            book_moves_limit=book_moves_limit,
        )
    hits, source = _combined_hits(explicit_hits=explicit_hits, lookup_hits=lookup_values)
    prefix = derive_book_prefix(moves_usi, hits, source=source)
    return _PairGame(
        game_id=game_id,
        order=display_order,
        black=black,
        white=white,
        initial_sfen=initial_sfen,
        matchup_key=matchup_key,
        pair_slot=pair_slot,
        pair_key=pair_key,
        prefix=prefix,
    )


def _pair_status(
    left: _PairGame,
    right: _PairGame | None,
    *,
    same_sfen: bool,
    ambiguous: bool,
) -> str:
    if right is None:
        return "unpaired"
    if not same_sfen:
        return "invalid_pair"
    if ambiguous:
        return "ambiguous"
    statuses = {left.prefix.measurement_status, right.prefix.measurement_status}
    if statuses == {"measured"}:
        return "measured"
    if "unmeasured" in statuses:
        return "unmeasured"
    return "partial"


def _pair_payload(left: _PairGame, right: _PairGame | None, *, ambiguous: bool = False) -> JsonObject:
    same_sfen = right is not None and left.initial_sfen == right.initial_sfen
    status = _pair_status(left, right, same_sfen=same_sfen, ambiguous=ambiguous)
    comparison = compare_book_prefixes(left.prefix, right.prefix) if right is not None and same_sfen else None
    payload: JsonObject = {
        "pair_key": left.pair_key,
        "matchup_key": left.matchup_key,
        "pair_slot": left.pair_slot,
        "orders": [left.order] if right is None else [left.order, right.order],
        "game_ids": [left.game_id] if right is None else [left.game_id, right.game_id],
        "same_sfen": same_sfen,
        "initial_sfen": left.initial_sfen,
        "left": left.side_payload(),
        "right": right.side_payload() if right is not None else None,
        "measurement_status": status,
    }
    if comparison is None:
        payload.update(
            {
                "matched_prefix_plies": 0,
                "first_diff_ply": None,
                "first_diff_reason": "unmeasured",
                "prefix_match_rate": None,
            }
        )
    else:
        payload.update(comparison.to_payload())
    return payload


def _first_order(item: JsonObject) -> int:
    """ペアのソートキーとして先頭の order を返す（欠損時は 0）。"""
    orders = item.get("orders")
    if isinstance(orders, list) and orders:
        first = orders[0]
        if isinstance(first, int):
            return first
    return 0


def _summary(pairs: Sequence[JsonObject]) -> JsonObject:
    rates: list[float] = []
    status_counts: dict[str, int] = {}
    for pair in pairs:
        status = coerce_str(pair.get("measurement_status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        rate = pair.get("prefix_match_rate")
        if status == "measured" and isinstance(rate, int | float):
            rates.append(float(rate))
    mean_rate: JsonValue = (sum(rates) / len(rates)) if rates else None
    return {
        "pairs": len(pairs),
        "measured_pairs": status_counts.get("measured", 0),
        "partial_pairs": status_counts.get("partial", 0),
        "unmeasured_pairs": status_counts.get("unmeasured", 0),
        "invalid_pairs": status_counts.get("invalid_pair", 0),
        "ambiguous_pairs": status_counts.get("ambiguous", 0),
        "unpaired_games": status_counts.get("unpaired", 0),
        "mean_prefix_match_rate": mean_rate,
    }


def build_book_pair_payload(
    game_query: DashboardGameQueryPort,
    db_path: Path,
    *,
    game_type: str = "arena",
    compute_out_of_book_plies: bool = False,
    allow_full_validation: bool = False,
    book_moves_limit: int = DEFAULT_BOOK_MOVES_LIMIT,
    book_opener: BookOpener | None = None,
) -> JsonObject:
    """Build pair-level book prefix diagnostics from recorded games."""

    opener = (
        book_opener
        if book_opener is not None
        else make_default_book_opener(allow_full_validation=allow_full_validation)
    )
    cache: dict[str, BookLookup | None] = {}
    pair_games: list[_PairGame] = []
    games_meta = game_query.load_games(db_path, game_type=game_type)
    for fallback_order, meta in enumerate(games_meta, start=1):
        black = meta.get("black_engine")
        white = meta.get("white_engine")
        result = meta.get("result")
        game_name = meta.get("game_name")
        if (
            not isinstance(black, str)
            or not isinstance(white, str)
            or not isinstance(result, GameResult)
            or not isinstance(game_name, str)
        ):
            continue
        record = game_query.load_game_record(db_path, game_name=game_name)
        if record is None:
            continue
        pair_games.append(
            _build_pair_game(
                game_id=game_name,
                order=fallback_order,
                black=black,
                white=white,
                initial_sfen=meta.get("initial_sfen") if isinstance(meta.get("initial_sfen"), str) else None,
                record=record,
                should_compute_lookup=compute_out_of_book_plies,
                opener=opener,
                cache=cache,
                book_moves_limit=book_moves_limit,
            )
        )

    groups: dict[tuple[str, int], list[_PairGame]] = {}
    for game in pair_games:
        groups.setdefault((game.matchup_key, game.pair_slot), []).append(game)

    pairs: list[JsonObject] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item.order)
        if len(ordered) == 1:
            pairs.append(_pair_payload(ordered[0], None))
            continue
        ambiguous = len(ordered) > 2
        for index in range(0, len(ordered), 2):
            left = ordered[index]
            right = ordered[index + 1] if index + 1 < len(ordered) else None
            pairs.append(_pair_payload(left, right, ambiguous=ambiguous and right is not None))

    pairs.sort(key=_first_order)
    return {
        "pairs": pairs,
        "summary": _summary(pairs),
        "note": "book prefix is based on explicit book_hit or book lookup membership; it is not proof the engine "
        "selected from book unless move_source/book_hit explicitly says so.",
    }


__all__ = ["build_book_pair_payload"]
