"""CLI commands for watching CSA bridge runs."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from shogiarena._core.contexts.csa_watch.application.game_export import export_games
from shogiarena._core.contexts.csa_watch.application.run_watcher import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CsaRunWatcher,
    RunView,
)
from shogiarena._core.contexts.csa_watch.application.status_report import render_status
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import CsaRecordFileWriterPort
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root

MIN_FOLLOW_INTERVAL_SECONDS = 0.2


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "csa",
        help="Inspect CSA protocol games recorded by rsshogi-csa-bridge",
        description="Read the JSONL event log a CSA bridge writes and report what it says.",
    )
    csa_sub = parser.add_subparsers(dest="csa_command")
    csa_sub.required = True
    _register_status(csa_sub)
    _register_export(csa_sub)


def _register_status(csa_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = csa_sub.add_parser(
        "status",
        help="Print phase, clocks, deadlines and alerts for CSA bridge runs",
    )
    parser.add_argument(
        "--csa-log-dir",
        required=True,
        type=Path,
        help="Directory containing {run_id}-events.jsonl files",
    )
    parser.add_argument("--run", dest="run_id", help="Restrict output to a single run id")
    parser.add_argument("--follow", action="store_true", help="Keep printing as the logs grow")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds when following (default: {DEFAULT_POLL_INTERVAL_SECONDS})",
    )
    parser.set_defaults(async_handler=_status_command)


def _resolve_log_dir(raw: Path) -> Path:
    log_dir = raw.expanduser()
    if not log_dir.is_dir():
        raise CliArgumentError(f"csa log directory not found: {log_dir}")
    return log_dir


def _select(views: tuple[RunView, ...], run_id: str | None) -> tuple[RunView, ...]:
    if run_id is None:
        return views
    return tuple(view for view in views if view.state.run_id == run_id)


async def _status_command(args: argparse.Namespace) -> None:
    log_dir = _resolve_log_dir(args.csa_log_dir)
    interval = max(MIN_FOLLOW_INTERVAL_SECONDS, float(args.interval or DEFAULT_POLL_INTERVAL_SECONDS))
    watcher = CsaRunWatcher(build_default_root().csa_log_source_factory(log_dir))
    try:
        await watcher.refresh()
        views = _select(watcher.views(), args.run_id)
        if args.run_id and not views:
            raise CliArgumentError(f"run not found in {log_dir}: {args.run_id}")
        _print_status(views, log_dir)
        if not args.follow:
            return
        while True:
            await asyncio.sleep(interval)
            await watcher.refresh()
            print()
            _print_status(_select(watcher.views(), args.run_id), log_dir)
    except (asyncio.CancelledError, KeyboardInterrupt):
        return
    finally:
        watcher.close()


def _print_status(views: tuple[RunView, ...], log_dir: Path) -> None:
    now_ms = int(time.time() * 1000)
    sys.stdout.write(render_status(views, now_ms=now_ms, log_dir=str(log_dir)))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _register_export(csa_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = csa_sub.add_parser(
        "export",
        help="Replay CSA games and write them as .csa records",
        description=(
            "Replays every move on a real board and writes only the games that survive it. "
            "The point is the audit: a game that cannot be reconstructed is reported, not written."
        ),
    )
    parser.add_argument("--csa-log-dir", required=True, type=Path, help="Directory containing event logs")
    parser.add_argument("--out", type=Path, help="Directory to write .csa files into (default: alongside nothing)")
    parser.add_argument("--run", dest="run_id", help="Restrict to a single run id")
    parser.add_argument(
        "--no-comments",
        action="store_true",
        help="Omit the evaluation comment lines",
    )
    parser.set_defaults(async_handler=_export_command)


def _resolve_out_dir(raw: Path | None, log_dir: Path) -> Path:
    out_dir = (raw or Path.cwd() / "csa-export").expanduser()
    if out_dir.resolve() == log_dir.resolve():
        # The log directory is the evidence for a game that may still be running.
        raise CliArgumentError("--out must not be the CSA log directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


async def _export_command(args: argparse.Namespace) -> None:
    log_dir = _resolve_log_dir(args.csa_log_dir)
    out_dir = _resolve_out_dir(args.out, log_dir)
    root = build_default_root()
    watcher = CsaRunWatcher(root.csa_log_source_factory(log_dir))
    try:
        await watcher.refresh()
        views = _select(watcher.views(), args.run_id)
        if args.run_id and not views:
            raise CliArgumentError(f"run not found in {log_dir}: {args.run_id}")
    finally:
        watcher.close()

    writer: CsaRecordFileWriterPort = root.csa_record_file_writer
    written = 0
    failed = 0
    for view in views:
        exported, failures = export_games(
            view.state.games,
            root.csa_board_replay,
            should_include_comments=not args.no_comments,
        )
        for game in exported:
            target = out_dir / game.file_name
            size = writer.write(game.record, target)
            written += 1
            sys.stdout.write(f"wrote {target} ({size} bytes)\n")
        for failure in failures:
            failed += 1
            sys.stderr.write(
                f"NOT WRITTEN {failure.game_id}: replay stopped at ply {failure.ply} "
                f"({failure.usi}): {failure.reason}\n"
            )
    sys.stdout.flush()
    sys.stderr.flush()
    if failed:
        # An unverifiable game is a finding, not a warning to scroll past.
        raise CliError(f"{failed} game(s) could not be replayed and were not written; {written} written")


__all__ = ["register"]
