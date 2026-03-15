from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.update_recording_service import (
    SpsaUpdateRecordingRequest,
    SpsaUpdateRecordingService,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


def test_record_emits_update_event_and_persists_index() -> None:
    service = SpsaUpdateRecordingService()
    events: list[JsonObject] = []
    persisted: list[tuple[SpsaUpdateRecordingRequest, int]] = []
    request = SpsaUpdateRecordingRequest(
        update_idx=12,
        params={"A": 1.25},
        s_plus=1.0,
        s_minus=0.0,
        step=1.0,
        gradients={"A": 0.5},
        deltas={"A": 0.25},
        delta_norm=0.25,
        batch_size=2,
        total_games=4,
        perturbations={"plus": {"A": 1.5}, "minus": {"A": 1.0}},
        c_k=0.5,
        a_k=0.3,
        iteration_k=12,
    )

    service.record(
        request=request,
        append_spsa_event=lambda payload: events.append(payload),
        persist_update_index=lambda req, ts: persisted.append((req, ts)),
    )

    assert len(events) == 1
    event = events[0]
    assert event["event"] == "update"
    assert event["update_idx"] == 12
    assert event["params"] == {"A": 1.25}
    assert event["gradients"] == {"A": 0.5}
    assert event["deltas"] == {"A": 0.25}
    assert event["delta_norm"] == 0.25
    assert event["batch_size"] == 2
    assert event["total_games"] == 4
    assert isinstance(event["timestamp"], int)

    assert len(persisted) == 1
    persisted_request, timestamp = persisted[0]
    assert persisted_request == request
    assert isinstance(timestamp, int)
