from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.event_payload_service import (
    SpsaScheduledEventCommonRequest,
    build_scheduled_event_common,
    build_status_payload,
)


def test_build_scheduled_event_common_builds_expected_shape() -> None:
    payload = build_scheduled_event_common(
        request=SpsaScheduledEventCommonRequest(
            update_idx=12,
            game_id="game-12",
            tuned_variant_token="v12",
            baseline_variant_token="v12b",
            variant_label="v12+",
            phase="plus",
            is_tuned_as_black=True,
            black_player="tuned-v12+",
            white_player="baseline-v12",
            assigned_instance="inst-a",
            worker_idx=3,
            event_family="ltc",
        )
    )

    assert payload["event"] == "game_scheduled"
    assert payload["update_idx"] == 12
    assert payload["game_id"] == "game-12"
    assert payload["variant_token"] == "v12"
    assert payload["tuned_variant_token"] == "v12"
    assert payload["baseline_variant_token"] == "v12b"
    assert payload["variant_label"] == "v12+"
    assert payload["phase"] == "plus"
    assert payload["tuned_as_black"] is True
    assert payload["black_player"] == "tuned-v12+"
    assert payload["white_player"] == "baseline-v12"
    assert payload["assigned_instance"] == "inst-a"
    assert payload["worker_idx"] == 3
    assert payload["family"] == "ltc"
    assert payload["is_ltc"] is True


def test_build_status_payload_overlays_status_without_mutating_source() -> None:
    event_common = {
        "event": "game_scheduled",
        "update_idx": 1,
        "worker_idx": 2,
    }

    payload = build_status_payload(
        event_common=event_common,
        status="running",
        start_time="2026-02-24T19:31:10+00:00",
    )

    assert payload["status"] == "running"
    assert payload["start_time"] == "2026-02-24T19:31:10+00:00"
    assert payload["worker_idx"] == 2
    assert "status" not in event_common
