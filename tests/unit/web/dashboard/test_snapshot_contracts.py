"""Dashboard backend snapshot contract tests."""

from __future__ import annotations

import pytest

from shogiarena._core.interfaces.boundaries.parsers.snapshot import parse_dashboard_snapshot_payload
from shogiarena._core.shared.kernel.exceptions import ContractParseError


def test_parse_summary_snapshot_preserves_json_object() -> None:
    raw: dict[str, object] = {
        "games": {"completed": 3, "total": 10},
        "is_summary_ready": True,
        "extra": "value",
    }
    parsed = parse_dashboard_snapshot_payload("summary", raw, path="tests/summary")

    assert parsed["games"] == {"completed": 3, "total": 10}
    assert parsed["is_summary_ready"] is True
    assert parsed["extra"] == "value"


def test_parse_summary_snapshot_rejects_non_json_object_root() -> None:
    with pytest.raises(ContractParseError):
        parse_dashboard_snapshot_payload("summary", [])  # type: ignore[arg-type]


def test_parse_games_snapshot_rejects_non_list_rows() -> None:
    with pytest.raises(ContractParseError):
        parse_dashboard_snapshot_payload(
            "games",
            {
                "kind": "bulk",
                "revision": 1,
                "base_revision": None,
                "rows": "not-list",
                "snapshot_meta": {},
            },
            path="tests/games",
        )


def test_parse_games_snapshot_rejects_invalid_row_entry() -> None:
    with pytest.raises(ContractParseError):
        parse_dashboard_snapshot_payload(
            "games",
            {
                "kind": "bulk",
                "revision": 2,
                "base_revision": None,
                "rows": [{"game_id": "g-1"}, "invalid", {"game_id": "g-2"}],
                "snapshot_meta": {"total": 2},
            },
            path="tests/games",
        )
