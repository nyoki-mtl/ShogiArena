from __future__ import annotations

import rshogi

from shogiarena._core.shared.kernel.record_engine_metrics import attach_engine_wall_times


def test_attach_engine_wall_times_rebuilds_record_payload_not_move_clones() -> None:
    record = rshogi.record.GameRecord.from_usi_main_line(
        "startpos",
        ["7g7f"],
        move_times_ms=[11],
        evals=[100],
        nodes=[1000],
        depths=[9],
        seldepths=[12],
        wall_times_ms=[21],
        latency_deltas_ms=[2],
    )

    updated = attach_engine_wall_times(record, [31])

    original_extras = record.to_dict()["moves"][0]["engine_info"]["extras"]
    updated_extras = updated.to_dict()["moves"][0]["engine_info"]["extras"]
    assert "engine_wall_time_ms" not in original_extras
    assert updated_extras["engine_wall_time_ms"] == 31
    assert updated.moves[0].engine_info.extras["engine_wall_time_ms"] == 31
