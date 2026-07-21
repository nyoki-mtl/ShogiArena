from __future__ import annotations

import rsshogi

from shogiarena._core.shared.kernel.record_engine_metrics import attach_engine_wall_times, attach_move_source_metadata


def _make_record() -> rsshogi.record.Record:
    return rsshogi.record.Record.from_usi_main_line(
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


def test_attach_engine_wall_times_rebuilds_record_payload_not_move_clones() -> None:
    record = _make_record()

    updated = attach_engine_wall_times(record, [31])

    original_extras = record.to_dict()["moves"][0]["engine_info"]["extras"]
    updated_extras = updated.to_dict()["moves"][0]["engine_info"]["extras"]
    assert "engine_wall_time_ms" not in original_extras
    assert updated_extras["engine_wall_time_ms"] == 31
    assert updated.moves[0].engine_info.extras["engine_wall_time_ms"] == 31


def test_attach_move_source_metadata_rebuilds_record_payload() -> None:
    record = _make_record()

    updated = attach_move_source_metadata(record, move_sources=["book"], book_hits=[True])

    extras = updated.to_dict()["moves"][0]["engine_info"]["extras"]
    assert extras["move_source"] == "book"
    assert extras["book_hit"] is True
    assert updated.moves[0].engine_info.extras["move_source"] == "book"
