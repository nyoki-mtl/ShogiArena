"""Render folded CSA run state as a handful of lines.

This is the answer to "is anything wrong right now" over ssh, where opening a
browser is not an option. Rendering is pure and the caller supplies the clock, so
the same state always produces the same text in a test.
"""

from __future__ import annotations

from collections.abc import Sequence

from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.run_state import (
    AlertEntry,
    GameState,
    LogHealth,
    PendingSearch,
    RunState,
)

MAX_RENDERED_ALERTS = 5
_LABEL_WIDTH = 12


def format_clock_ms(remaining_ms: int | None) -> str:
    """``1:19`` style clock. Negative input is clamped: a clock never runs backwards."""
    if remaining_ms is None:
        return "--:--"
    total_seconds = max(0, remaining_ms) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_duration_ms(elapsed_ms: int | None) -> str:
    if elapsed_ms is None:
        return "unknown"
    total_seconds = max(0, elapsed_ms) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _line(label: str, value: str) -> str:
    return f"  {label.ljust(_LABEL_WIDTH)}{value}"


def _phase_line(state: RunState, now_ms: int | None) -> str:
    phase = state.phase or "unknown"
    if state.phase_since_ts is None or now_ms is None:
        return _line("phase", phase)
    return _line("phase", f"{phase}  ({format_duration_ms(now_ms - state.phase_since_ts)})")


def _deadline_line(pending: PendingSearch | None, now_ms: int | None) -> str | None:
    if pending is None or pending.deadline_ts is None:
        return None
    if now_ms is None:
        return _line("deadline", f"{pending.kind} at ply {pending.ply}")
    remaining_ms = pending.deadline_ts - now_ms
    if remaining_ms < 0:
        return _line("deadline", f"OVERDUE by {format_duration_ms(-remaining_ms)} ({pending.kind})")
    return _line("deadline", f"{remaining_ms / 1000:.1f}s left ({pending.kind})")


def _game_lines(game: GameState, now_ms: int | None) -> list[str]:
    lines = [
        _line("game", f"{game.black_name} (b) vs {game.white_name} (w)"),
        _line("", game.game_id),
    ]
    if game.is_finished:
        lines.append(_line("ply", str(game.current_ply)))
        terminal = " ".join(game.terminal) if game.terminal else "-"
        lines.append(_line("result", f"{game.result}  {terminal}"))
    else:
        lines.append(_line("ply", f"{game.current_ply}  ({game.side_to_move} to move)"))
    lines.append(
        _line(
            "clock",
            f"black {format_clock_ms(game.black_remaining_ms)}   white {format_clock_ms(game.white_remaining_ms)}"
            "   (ledger)",
        )
    )
    deadline = _deadline_line(game.pending, now_ms)
    if deadline is not None:
        lines.append(deadline)
    fallback = game.fallback_plies
    if fallback:
        lines.append(_line("fallback", f"{len(fallback)} move(s), latest ply {fallback[-1]}"))
    return lines


def _alert_lines(alerts: Sequence[AlertEntry]) -> list[str]:
    if not alerts:
        return [_line("alerts", "none")]
    lines: list[str] = []
    for index, alert in enumerate(alerts[:MAX_RENDERED_ALERTS]):
        detail = f": {alert.detail}" if alert.detail else ""
        lines.append(_line("alerts" if index == 0 else "", f"[{alert.level}] {alert.code}{detail}"))
    hidden = len(alerts) - MAX_RENDERED_ALERTS
    if hidden > 0:
        lines.append(_line("", f"... {hidden} more"))
    return lines


def _health_line(health: LogHealth, *, has_partial_line: bool) -> str:
    parts: list[str] = []
    if health.missing_seq:
        parts.append(f"missing seq {health.missing_seq}")
    if health.malformed:
        parts.append(f"malformed {health.malformed}")
    if health.invalid_lines:
        parts.append(f"unreadable lines {health.invalid_lines}")
    if health.orphan_records:
        parts.append(f"orphan records {health.orphan_records}")
    if health.ply_gaps:
        parts.append(f"ply gaps {health.ply_gaps}")
    for record_type, count in sorted(health.unknown_types.items()):
        parts.append(f"unknown '{record_type}' x{count}")
    if has_partial_line:
        parts.append("trailing partial line (writer stopped mid-record)")
    return _line("log", ", ".join(parts) if parts else "clean")


def render_run(view: RunView, *, now_ms: int | None = None) -> str:
    state = view.state
    header = f"run {state.run_id}  worker {view.worker_idx}"
    if state.version:
        header = f"{header}  bridge {state.version}"
    lines = [header, _phase_line(state, now_ms)]

    game = state.current_game or (state.games[-1] if state.games else None)
    if game is None:
        lines.append(_line("game", "none yet"))
    else:
        lines.extend(_game_lines(game, now_ms))

    score = state.score
    lines.append(_line("record", f"{score.wins}W {score.losses}L {score.draws}D  ({len(state.games)} game(s))"))

    ponder = state.ponder
    if ponder.started:
        rate = ponder.hit_rate
        rate_text = "n/a" if rate is None else f"{rate * 100:.0f}%"
        lines.append(_line("ponder", f"{ponder.hits}/{ponder.started} hit {rate_text}  unresolved {ponder.unresolved}"))

    lines.extend(_alert_lines(state.sorted_alerts()))
    lines.append(_health_line(state.health, has_partial_line=view.has_partial_line))
    return "\n".join(lines)


def render_status(views: Sequence[RunView], *, now_ms: int | None = None, log_dir: str | None = None) -> str:
    if not views:
        location = f" under {log_dir}" if log_dir else ""
        return f"No CSA event logs found{location}."
    return "\n\n".join(render_run(view, now_ms=now_ms) for view in views)


__all__ = [
    "MAX_RENDERED_ALERTS",
    "format_clock_ms",
    "format_duration_ms",
    "render_run",
    "render_status",
]
