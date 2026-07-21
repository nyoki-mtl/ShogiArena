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


def test_usi_transcript_stops_at_byte_cap_with_explicit_marker(tmp_path: Path) -> None:
    path = tmp_path / "bounded.log"
    writer = UsiTranscriptWriter(
        path=path,
        game_id="g0001",
        role="black",
        engine_name="engine-a",
        initial_sfen="startpos",
        detail="commands_and_info",
        max_bytes=512,
    )

    for index in range(100):
        writer.handle(_event("in", f"info depth {index} string {'x' * 80}", timestamp_ms=index))
    writer.close()

    data = path.read_bytes()
    text = data.decode("utf-8")
    assert len(data) <= 512
    assert "marker black truncated max_bytes=512" in text
    assert "info depth 99" not in text


def test_usi_transcript_never_truncates_silently_with_a_tiny_cap(tmp_path: Path) -> None:
    # 打ち切りマーカーより小さい上限を渡されても、打ち切りの事実は必ず記録する。
    path = tmp_path / "tiny.log"
    writer = UsiTranscriptWriter(
        path=path,
        game_id="g0001",
        role="black",
        engine_name="engine-a",
        initial_sfen="startpos",
        detail="commands_and_info",
        max_bytes=1,
    )

    for index in range(10):
        writer.handle(_event("in", f"info depth {index}", timestamp_ms=index))
    writer.close()

    text = path.read_text(encoding="utf-8")
    assert "marker black truncated" in text


def test_usi_transcript_omits_close_marker_when_truncated(tmp_path: Path) -> None:
    # 打ち切り時は close marker を書かないので、読み手は
    # 「truncated あり + close なし」で不完全な transcript を判別できる。
    path = tmp_path / "no-close.log"
    writer = UsiTranscriptWriter(
        path=path,
        game_id="g0001",
        role="white",
        engine_name="engine-b",
        initial_sfen="startpos",
        detail="commands_and_info",
        max_bytes=512,
    )

    for index in range(100):
        writer.handle(_event("in", f"info depth {index} string {'x' * 80}", timestamp_ms=index))
    writer.close()

    text = path.read_text(encoding="utf-8")
    assert "marker white truncated" in text
    assert "marker white close" not in text
