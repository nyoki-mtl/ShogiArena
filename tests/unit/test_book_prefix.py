from __future__ import annotations

from shogiarena._core.shared.kernel.book_prefix import compare_book_prefixes, derive_book_prefix


def test_compare_book_prefix_reports_first_diff() -> None:
    left = derive_book_prefix(["7g7f", "3c3d"], [True, True], source="db_book_hit")
    right = derive_book_prefix(["7g7f", "8c8d"], [True, True], source="db_book_hit")

    comparison = compare_book_prefixes(left, right)

    assert comparison.matched_prefix_plies == 1
    assert comparison.first_diff_ply == 2
    assert comparison.first_diff_reason == "move_mismatch"
    assert comparison.prefix_match_rate == 0.5


def test_unknown_hit_keeps_prefix_partial() -> None:
    prefix = derive_book_prefix(["7g7f", "3c3d"], [True, None], source="db_book_hit")

    assert prefix.moves_usi == ("7g7f",)
    assert prefix.measurement_status == "partial"
