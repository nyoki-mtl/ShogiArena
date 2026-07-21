from __future__ import annotations

from types import SimpleNamespace

from shogiarena._core.contexts.dashboard.adapters.spsa.update_detail_builder import SpsaUpdateDetailBuilder
from shogiarena._core.contexts.spsa.application.dashboard.event_updates_collection import collect_updates_from_events
from shogiarena._core.contexts.spsa.application.dashboard.index_update_merge import merge_index_updates


def test_update_detail_reports_ltc_regression_presence(tmp_path) -> None:
    builder = SpsaUpdateDetailBuilder(
        store=SimpleNamespace(read_spsa_engine_names=lambda: ("base", "tuned")),
        db_path=tmp_path / "missing.sqlite3",
        game_event_snapshot_loader=lambda _game_id: None,
    )

    detail = builder.build(
        idx=1,
        detail_state={
            "update_idx": 1,
            "ltc_regression": {"status": "running"},
        },
    )

    assert detail["ltc_regression"] == {"status": "running"}
    assert detail["has_ltc_regression"] is True
    assert detail["payload"]["has_ltc_regression"] is True


def test_collect_updates_from_events_reports_ltc_regression_presence() -> None:
    updates = collect_updates_from_events(
        [
            {
                "event": "ltc_regression_start",
                "update_idx": 1,
                "ts": 100,
                "total_pairs": 4,
            }
        ],
        now_ts=100,
    )

    assert updates[0]["ltc_regression"]["status"] == "running"
    assert updates[0]["has_ltc_regression"] is True


def test_merge_index_updates_reports_event_only_ltc_regression_presence() -> None:
    merged = merge_index_updates(
        index_updates=[{"update_idx": 1}],
        event_updates=[{"update_idx": 2, "ltc_regression": {"status": "running"}}],
        format_variant_label=lambda value: f"v{value}",
        parse_ltc_regression_detail=lambda value: value,
        normalize_non_negative_idx=lambda value: value if isinstance(value, int) and value >= 0 else None,
    )

    event_only_entry = next(entry for entry in merged if entry["update_idx"] == 2)
    assert event_only_entry["has_ltc_regression"] is True
