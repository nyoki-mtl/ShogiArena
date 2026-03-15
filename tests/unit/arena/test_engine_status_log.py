from shogiarena._core.contexts.game_session.application.progress.model_mutations import update_engine_status_model_entry
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import WorkerSnapshotModel
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import (
    sanitize_engine_io_line_optional,
)


def test_sanitize_engine_io_line_does_not_truncate() -> None:
    raw = "x" * 1200
    assert sanitize_engine_io_line_optional(raw) == raw


def test_update_engine_status_entry_keeps_full_line() -> None:
    snapshot = WorkerSnapshotModel(
        game_id="g1",
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        current_ply=0,
        sfen="startpos",
    )
    line = "go " + ("movetime 1 " * 200)
    update_engine_status_model_entry(snapshot, "black", direction="out", line=line, state="ready", timestamp=1234)

    status = snapshot.engine_status
    black = status.get("black")
    assert black is not None
    io_tail = black.io_tail
    assert io_tail
    first = io_tail[0]
    assert first.line == line.strip()
