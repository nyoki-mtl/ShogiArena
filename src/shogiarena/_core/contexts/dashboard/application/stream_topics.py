"""Shared topic parsing/building helpers for dashboard live streams."""

from __future__ import annotations

_LIVE_GAME_PREFIX = "live.game."
_LIVE_ENGINE_PREFIX = "live.engine."


def parse_live_game_gid(topic: str) -> str | None:
    """Parse gid from live.game topic."""
    if not topic.startswith(_LIVE_GAME_PREFIX):
        return None
    rest = topic[len(_LIVE_GAME_PREFIX) :]
    if rest.endswith(".snapshot"):
        return rest[: -len(".snapshot")].strip(".") or None
    for suffix in (
        ".engine_status.diff",
        ".analysis.diff",
        ".moves.diff",
        ".clock.diff",
        ".meta.diff",
    ):
        if rest.endswith(suffix):
            return rest[: -len(suffix)].strip(".") or None
    return None


def parse_live_engine_io_target(topic: str) -> tuple[str, str] | None:
    """Parse (gid, role) from live.engine.*.io.* topic."""
    if not topic.startswith(_LIVE_ENGINE_PREFIX):
        return None
    rest = topic[len(_LIVE_ENGINE_PREFIX) :]
    if ".io." not in rest:
        return None
    left, _suffix = rest.rsplit(".io.", 1)
    parts = left.split(".")
    if len(parts) < 2:
        return None
    role = parts[-1]
    gid = ".".join(parts[:-1])
    if role not in {"black", "white"} or not gid:
        return None
    return gid, role


def topic_live_game_snapshot(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.snapshot"


def topic_live_game_moves_diff(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.moves.diff"


def topic_live_game_analysis_diff(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.analysis.diff"


def topic_live_game_meta_diff(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.meta.diff"


def topic_live_game_clock_diff(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.clock.diff"


def topic_live_game_engine_status_diff(gid: str) -> str:
    return f"{_LIVE_GAME_PREFIX}{gid}.engine_status.diff"


def topic_live_engine_io_snapshot(gid: str, role: str) -> str:
    return f"{_LIVE_ENGINE_PREFIX}{gid}.{role}.io.snapshot"


__all__ = [
    "parse_live_engine_io_target",
    "parse_live_game_gid",
    "topic_live_game_clock_diff",
    "topic_live_game_engine_status_diff",
    "topic_live_engine_io_snapshot",
    "topic_live_game_analysis_diff",
    "topic_live_game_meta_diff",
    "topic_live_game_moves_diff",
    "topic_live_game_snapshot",
]
