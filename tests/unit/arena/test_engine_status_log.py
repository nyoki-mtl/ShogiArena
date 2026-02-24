from shogiarena.arena.orchestrators.base_orchestrator_utils import sanitize_engine_io_line, update_engine_status_entry


def test_sanitize_engine_io_line_does_not_truncate() -> None:
    raw = "x" * 1200
    assert sanitize_engine_io_line(raw) == raw


def test_update_engine_status_entry_keeps_full_line() -> None:
    snapshot: dict[str, object] = {}
    line = "go " + ("movetime 1 " * 200)
    update_engine_status_entry(snapshot, "black", direction="out", line=line, state="ready", timestamp=1234)

    status = snapshot.get("engine_status")
    assert isinstance(status, dict)
    black = status.get("black")
    assert isinstance(black, dict)
    io_tail = black.get("io_tail")
    assert isinstance(io_tail, list)
    assert io_tail
    first = io_tail[0]
    assert isinstance(first, dict)
    assert first.get("line") == line.strip()
