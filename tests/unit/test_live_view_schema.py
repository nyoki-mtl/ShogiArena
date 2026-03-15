from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import (
    attach_live_view_payload,
    build_live_view_snapshot,
)


def test_build_live_view_snapshot_spsa_progress_has_completed_total_defaults():
    snapshot = build_live_view_snapshot(
        {
            "tournamentType": "spsa",
            "games": {"completed": 0, "total": 0},
            "timestamp": "2025-12-13T00:00:00Z",
        },
    )
    assert snapshot["mode"] == "spsa"
    assert snapshot["progress"] is not None
    assert snapshot["progress"]["kind"] == "updates"
    assert snapshot["progress"]["completed"] == 0
    assert snapshot["progress"]["total"] == 0


def test_build_live_view_snapshot_spsa_progress_preserves_values():
    snapshot = build_live_view_snapshot(
        {"tournamentType": "spsa", "games": {"completed": 12, "total": 34}, "timestamp": "2025-12-13T00:00:00Z"},
    )
    assert snapshot["mode"] == "spsa"
    assert snapshot["progress"]["completed"] == 12
    assert snapshot["progress"]["total"] == 34


def test_attach_live_view_payload_adds_tournament_progress_snapshot():
    payload = attach_live_view_payload(
        {
            "summarySource": "tournament",
            "mode": "tournament",
            "games": {"completed": 5, "total": 8, "cancelled": 0},
            "timestamp": "2026-03-11T00:00:00Z",
        }
    )

    assert payload["liveView"]["mode"] == "tournament"
    assert payload["liveView"]["progress"]["kind"] == "games"
    assert payload["liveView"]["progress"]["completed"] == 5
    assert payload["liveView"]["progress"]["total"] == 8
