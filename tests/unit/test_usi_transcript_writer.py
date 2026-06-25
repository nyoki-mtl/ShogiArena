from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.usi_transcript import UsiTranscriptWriter
from shogiarena._core.shared.kernel.engine_io import UsiIoDirection, UsiIoEvent


def _event(
    direction: UsiIoDirection,
    line: str,
    *,
    timestamp_ms: int,
    phase: str | None = None,
) -> UsiIoEvent:
    return UsiIoEvent(direction=direction, line=line, timestamp_ms=timestamp_ms, phase=phase)


def test_usi_transcript_commands_mode_keeps_final_non_bound_info(tmp_path: Path) -> None:
    path = tmp_path / "transcript.log"
    writer = UsiTranscriptWriter(
        path=path,
        game_id="g0001",
        role="black",
        engine_name="engine-a",
        initial_sfen="startpos",
        detail="commands",
    )

    writer.handle(_event("out", "position startpos", timestamp_ms=1000, phase="ready"))
    writer.handle(_event("out", "go nodes 10", timestamp_ms=1010, phase="ready"))
    writer.handle(_event("in", "info depth 3 score cp 100 lowerbound pv 7g7f", timestamp_ms=1020))
    writer.handle(_event("in", "info depth 4 score cp 80 pv 7g7f", timestamp_ms=1030))
    writer.handle(_event("in", "bestmove 7g7f", timestamp_ms=1040))
    writer.close()

    text = path.read_text(encoding="utf-8")

    assert "position startpos" in text
    assert "go nodes 10" in text
    assert "lowerbound" not in text
    assert "info depth 4 score cp 80 pv 7g7f" in text
    assert "bestmove 7g7f" in text


def test_usi_transcript_commands_and_info_mode_keeps_all_info(tmp_path: Path) -> None:
    path = tmp_path / "transcript.log"
    writer = UsiTranscriptWriter(
        path=path,
        game_id="g0001",
        role="white",
        engine_name="engine-b",
        initial_sfen="startpos",
        detail="commands_and_info",
    )

    writer.handle(_event("in", "info depth 1 score cp 10 lowerbound", timestamp_ms=1000))
    writer.handle(_event("in", "info depth 2 score cp 20", timestamp_ms=1010))
    writer.handle(_event("in", "bestmove 3c3d", timestamp_ms=1020))
    writer.close()

    text = path.read_text(encoding="utf-8")

    assert "info depth 1 score cp 10 lowerbound" in text
    assert "info depth 2 score cp 20" in text
    assert "bestmove 3c3d" in text
