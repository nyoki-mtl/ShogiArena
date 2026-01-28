from shogiarena.web.dashboard.backend.live import build_live_view_snapshot


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
