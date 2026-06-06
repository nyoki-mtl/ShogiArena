from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.usi_transcript import UsiTranscriptWriter


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

    writer.handle({"dir": "out", "line": "position startpos", "ts": 1000, "state": "ready"})
    writer.handle({"dir": "out", "line": "go nodes 10", "ts": 1010, "state": "ready"})
    writer.handle({"dir": "in", "line": "info depth 3 score cp 100 lowerbound pv 7g7f", "ts": 1020})
    writer.handle({"dir": "in", "line": "info depth 4 score cp 80 pv 7g7f", "ts": 1030})
    writer.handle({"dir": "in", "line": "bestmove 7g7f", "ts": 1040})
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

    writer.handle({"dir": "in", "line": "info depth 1 score cp 10 lowerbound", "ts": 1000})
    writer.handle({"dir": "in", "line": "info depth 2 score cp 20", "ts": 1010})
    writer.handle({"dir": "in", "line": "bestmove 3c3d", "ts": 1020})
    writer.close()

    text = path.read_text(encoding="utf-8")

    assert "info depth 1 score cp 10 lowerbound" in text
    assert "info depth 2 score cp 20" in text
    assert "bestmove 3c3d" in text
