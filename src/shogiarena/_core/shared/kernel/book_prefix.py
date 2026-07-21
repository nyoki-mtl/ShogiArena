"""Pure helpers for opening book prefix diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

BookPrefixMeasurementSource = Literal["db_book_hit", "book_lookup", "mixed", "unmeasured"]
BookPrefixMeasurementStatus = Literal["measured", "partial", "unmeasured"]
FirstDiffReason = Literal["move_mismatch", "length_mismatch", "unmeasured", "same_prefix"]


@dataclass(frozen=True, slots=True)
class BookPrefix:
    """Continuous in-book prefix for one game."""

    moves_usi: tuple[str, ...]
    measurement_source: BookPrefixMeasurementSource
    measurement_status: BookPrefixMeasurementStatus

    @property
    def length(self) -> int:
        return len(self.moves_usi)

    def to_payload(self) -> JsonObject:
        return {
            "book_prefix_usi": list(self.moves_usi),
            "book_prefix_length": len(self.moves_usi),
            "measurement_source": self.measurement_source,
            "measurement_status": self.measurement_status,
        }


@dataclass(frozen=True, slots=True)
class BookPrefixComparison:
    """Prefix comparison result for two paired games."""

    matched_prefix_plies: int
    first_diff_ply: int | None
    first_diff_reason: FirstDiffReason
    prefix_match_rate: float | None

    def to_payload(self) -> JsonObject:
        match_rate: JsonValue = self.prefix_match_rate
        return {
            "matched_prefix_plies": self.matched_prefix_plies,
            "first_diff_ply": self.first_diff_ply,
            "first_diff_reason": self.first_diff_reason,
            "prefix_match_rate": match_rate,
        }


def derive_book_prefix(
    moves_usi: Sequence[str],
    book_hits: Sequence[bool | None],
    *,
    source: BookPrefixMeasurementSource,
) -> BookPrefix:
    """Derive the continuous book prefix from per-ply hit observations."""

    prefix: list[str] = []
    observed = False
    for move_usi, hit in zip(moves_usi, book_hits, strict=False):
        if hit is None:
            status: BookPrefixMeasurementStatus = "partial" if observed else "unmeasured"
            return BookPrefix(tuple(prefix), "unmeasured" if not observed else source, status)
        observed = True
        if not hit:
            return BookPrefix(tuple(prefix), source, "measured")
        prefix.append(move_usi)

    if not moves_usi or not observed:
        return BookPrefix(tuple(prefix), "unmeasured", "unmeasured")
    if len(book_hits) < len(moves_usi):
        return BookPrefix(tuple(prefix), source, "partial")
    return BookPrefix(tuple(prefix), source, "measured")


def compare_book_prefixes(left: BookPrefix, right: BookPrefix) -> BookPrefixComparison:
    """Compare two book prefixes and return first-diff diagnostics."""

    if left.measurement_status == "unmeasured" or right.measurement_status == "unmeasured":
        return BookPrefixComparison(
            matched_prefix_plies=0,
            first_diff_ply=None,
            first_diff_reason="unmeasured",
            prefix_match_rate=None,
        )

    matched = 0
    for left_move, right_move in zip(left.moves_usi, right.moves_usi, strict=False):
        if left_move != right_move:
            max_len = max(left.length, right.length)
            return BookPrefixComparison(
                matched_prefix_plies=matched,
                first_diff_ply=matched + 1,
                first_diff_reason="move_mismatch",
                prefix_match_rate=(matched / max_len) if max_len else None,
            )
        matched += 1

    max_len = max(left.length, right.length)
    if left.length != right.length:
        return BookPrefixComparison(
            matched_prefix_plies=matched,
            first_diff_ply=matched + 1,
            first_diff_reason="length_mismatch",
            prefix_match_rate=(matched / max_len) if max_len else None,
        )
    return BookPrefixComparison(
        matched_prefix_plies=matched,
        first_diff_ply=None,
        first_diff_reason="same_prefix",
        prefix_match_rate=(matched / max_len) if max_len else None,
    )


__all__ = [
    "BookPrefix",
    "BookPrefixComparison",
    "BookPrefixMeasurementSource",
    "BookPrefixMeasurementStatus",
    "FirstDiffReason",
    "compare_book_prefixes",
    "derive_book_prefix",
]
