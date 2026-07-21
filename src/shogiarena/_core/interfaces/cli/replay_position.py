"""CLI command for replaying a saved tournament position."""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rsshogi.core import Board, Move

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest, UsiThinkResultPort
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.interfaces.cli.option_parsing import parse_option_overrides
from shogiarena._core.interfaces.cli.run.analyze import parse_position_argument
from shogiarena._core.interfaces.cli.run.engine_loader import load_engine
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_manifest_reader import read_run_manifest
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.usi_transcript_contract import (
    TranscriptRole,
    safe_transcript_game_id,
    transcript_path_for_game,
)

ReplayMode = Literal["fresh", "replay_history"]

_TRANSCRIPT_LINE_RE = re.compile(r"^\s*\d+ms\s+(?P<direction>\S+)\s+(?P<state>\S+)\s+(?P<line>.*)$")


@dataclass(frozen=True, slots=True)
class SearchTrace:
    """Transcript 由来の 1 search コマンド。"""

    position_command: str
    go_command: str
    sfen: str
    moves: tuple[Move, ...]
    request: UsiThinkRequest


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    """replay 対象局面。"""

    game_id: str
    ply: int
    initial_sfen: str
    black_player: str
    white_player: str
    role: TranscriptRole
    position_command: str
    sfen: str
    moves_before: tuple[Move, ...]
    saved_move: Move | None


@dataclass(frozen=True, slots=True)
class ResolvedEngine:
    """CLI 指定から解決した engine 起動情報。"""

    label: str
    engine_argument: str
    extra_options: JsonObject | None


@dataclass(frozen=True, slots=True)
class ReplaySearchSummary:
    """replay search の表示用 summary。"""

    engine: str
    mode: ReplayMode
    go_command: str
    bestmove: str
    saved_move: str | None
    is_match: bool | None
    score: str | None
    score_type: str | None
    depth: int | None
    seldepth: int | None
    nodes: int | None
    nps: int | None
    hashfull: int | None
    pv: str | None
    transcript_path: Path | None
    replayed_searches: int
    skipped_ponder_searches: int


class _ReplayTranscriptWriter:
    """replay-position 実行時の任意 transcript を書き出す。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._start_ts_ms: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")
        self._handle.write("# ShogiArena replay-position transcript\n")

    def handle(self, entry: UsiIoEvent) -> None:
        line = entry.line
        if not line:
            return
        timestamp = entry.timestamp_ms or 0
        if self._start_ts_ms is None:
            self._start_ts_ms = timestamp
        elapsed_ms = max(0, timestamp - self._start_ts_ms)
        direction = entry.direction
        state = entry.phase or "-"
        self._handle.write(f"{elapsed_ms:010d}ms {direction} {state} {line}\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`replay-position` サブコマンドを登録する。"""

    parser = subparsers.add_parser(
        "replay-position",
        help="Replay a saved game position with a USI engine",
    )
    parser.add_argument("--run-dir", required=True, type=Path, help="Run directory containing game.db")
    parser.add_argument("--game-id", required=True, help="Saved game name or numeric DB id")
    parser.add_argument("--ply", required=True, type=int, help="1-based ply to replay")
    parser.add_argument("--engine", help="Engine name from manifest, engine YAML, or binary path")
    parser.add_argument("--compare", nargs=2, metavar=("ENGINE_A", "ENGINE_B"), help="Replay with two engines")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--fresh", action="store_true", help="Analyze the target position from a clean state")
    mode_group.add_argument(
        "--replay-history",
        action="store_true",
        help="Replay previous searches from the per-game transcript before the target ply",
    )
    parser.add_argument("--nodes", type=int, help="Search node limit for the target replay")
    parser.add_argument("--depth", type=int, help="Search depth limit for the target replay")
    parser.add_argument("--movetime", type=int, help="Fixed movetime in milliseconds for the target replay")
    parser.add_argument("--infinite", action="store_true", help="Run an infinite target search")
    parser.add_argument("--timeout", type=float, default=None, help="Timeout in seconds waiting for bestmove")
    parser.add_argument("--searchmoves", nargs="*", help="Restrict target search to specific moves")
    parser.add_argument("--emit-transcript", action="store_true", help="Write replay command/output transcript")
    parser.add_argument("--option", action="append", metavar="KEY=VALUE", help="Override engine option")
    parser.set_defaults(async_handler=_command)


async def _command(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise CliArgumentError(f"run directory not found: {run_dir}")
    db_path = run_dir / "game.db"
    if not db_path.exists():
        raise CliArgumentError(f"game.db not found in run directory: {run_dir}")
    if args.ply <= 0:
        raise CliArgumentError("--ply must be >= 1")

    target = _load_replay_target(run_dir=run_dir, game_id=str(args.game_id), ply=int(args.ply))
    mode: ReplayMode = "replay_history" if args.replay_history else "fresh"
    traces = _load_transcript_traces(run_dir=run_dir, game_id=target.game_id, role=target.role)
    target_trace = _find_trace_for_ply(traces, target.ply)
    target_request = _target_request_from_args(args, fallback=target_trace.request if target_trace else None)

    engine_args = tuple(args.compare) if args.compare else (args.engine,)
    if any(item is None or not str(item).strip() for item in engine_args):
        raise CliArgumentError("--engine is required unless --compare is used")
    option_overrides = _parse_engine_option_overrides(args.option)
    resolved_engines = [
        _resolve_engine(run_dir=run_dir, engine_argument=str(item), option_overrides=option_overrides)
        for item in engine_args
        if item is not None
    ]

    summaries: list[ReplaySearchSummary] = []
    for resolved in resolved_engines:
        summary = await _run_replay_for_engine(
            args=args,
            target=target,
            mode=mode,
            traces=traces,
            request=target_request,
            engine=resolved,
            run_dir=run_dir,
        )
        summaries.append(summary)

    _print_replay_report(target=target, summaries=summaries)


def _load_replay_target(*, run_dir: Path, game_id: str, ply: int) -> ReplayTarget:
    record = _load_game_record(run_dir=run_dir, game_id=game_id)
    if record is None:
        raise CliArgumentError(f"game not found in {run_dir / 'game.db'}: {game_id}")
    initial_sfen = coerce_str(getattr(record, "init_position_sfen", None))
    if initial_sfen is None:
        raise CliError("saved game has no initial SFEN")
    moves = _record_moves(record)
    if ply > len(moves) + 1:
        raise CliArgumentError(f"--ply {ply} is beyond the saved game length ({len(moves)} moves)")

    board = Board()
    board.set_sfen(initial_sfen)
    for move in moves[: ply - 1]:
        board.apply_move(move)
    role: TranscriptRole = "black" if board.turn.is_black() else "white"
    saved_move = moves[ply - 1] if ply <= len(moves) else None

    metadata = getattr(record, "metadata", None)
    black_player = coerce_str(getattr(metadata, "black_player", None)) or ""
    white_player = coerce_str(getattr(metadata, "white_player", None)) or ""
    resolved_game_id = coerce_str(getattr(record, "game_name", None)) or game_id
    return ReplayTarget(
        game_id=resolved_game_id,
        ply=ply,
        initial_sfen=initial_sfen,
        black_player=black_player,
        white_player=white_player,
        role=role,
        position_command=_build_position_command(initial_sfen, moves[: ply - 1]),
        sfen=initial_sfen,
        moves_before=tuple(moves[: ply - 1]),
        saved_move=saved_move,
    )


def _load_game_record(*, run_dir: Path, game_id: str) -> object | None:
    loader = build_default_root().game_record_loader
    db_path = run_dir / "game.db"
    if game_id.isdigit():
        record = loader(db_path, game_id=int(game_id))
        if record is not None:
            return record
    return loader(db_path, game_name=game_id)


def _record_moves(record: object) -> list[Move]:
    raw_moves = getattr(record, "moves", ())
    moves: list[Move] = []
    for move_record in raw_moves:
        move = getattr(move_record, "move", None)
        if isinstance(move, Move):
            moves.append(move)
            continue
        to_move = getattr(move, "to_move", None)
        if callable(to_move):
            converted = to_move()
            if isinstance(converted, Move):
                moves.append(converted)
                continue
        raise CliError("saved game contains an unsupported move record")
    return moves


def _build_position_command(initial_sfen: str, moves: Sequence[Move]) -> str:
    if initial_sfen == "startpos":
        command = "position startpos"
    else:
        command = f"position sfen {initial_sfen}"
    if moves:
        command += " moves " + " ".join(move.to_usi() for move in moves)
    return command


def _load_transcript_traces(*, run_dir: Path, game_id: str, role: TranscriptRole) -> list[SearchTrace]:
    path = transcript_path_for_game(run_dir, game_id=game_id, role=role)
    if not path.exists():
        safe_id = safe_transcript_game_id(game_id)
        candidates = sorted((run_dir / "transcripts").glob(f"*{safe_id}*{role}.log"))
        path = candidates[0] if candidates else path
    if not path.exists():
        return []

    traces: list[SearchTrace] = []
    current_position: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = _TRANSCRIPT_LINE_RE.match(raw_line)
        if not match or match.group("direction") != "out":
            continue
        command = match.group("line").strip()
        if command.startswith("position "):
            current_position = command
            continue
        if command.startswith("go ") and current_position is not None:
            sfen, moves = parse_position_argument(current_position)
            traces.append(
                SearchTrace(
                    position_command=current_position,
                    go_command=command,
                    sfen=sfen,
                    moves=moves,
                    request=parse_go_command(command),
                )
            )
    return traces


def _find_trace_for_ply(traces: Sequence[SearchTrace], ply: int) -> SearchTrace | None:
    moves_before = ply - 1
    for trace in traces:
        if len(trace.moves) == moves_before:
            return trace
    return None


def parse_go_command(command: str) -> UsiThinkRequest:
    """USI `go` コマンド文字列を UsiThinkRequest に戻す。"""

    tokens = command.strip().split()
    if not tokens or tokens[0] != "go":
        raise CliArgumentError(f"expected go command: {command}")

    values: dict[str, int] = {}
    is_ponder = False
    is_infinite = False
    searchmoves: list[Move] = []
    value_tokens = {"movetime", "btime", "wtime", "binc", "winc", "byoyomi", "depth", "nodes"}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "ponder":
            is_ponder = True
            index += 1
            continue
        if token == "infinite":
            is_infinite = True
            index += 1
            continue
        if token == "searchmoves":
            for raw_move in tokens[index + 1 :]:
                searchmoves.append(Move.from_usi(raw_move))
            break
        if token in value_tokens:
            if index + 1 >= len(tokens):
                raise CliArgumentError(f"go {token} requires a value")
            try:
                values[token] = int(tokens[index + 1])
            except ValueError as exc:
                raise CliArgumentError(f"go {token} must be an integer") from exc
            index += 2
            continue
        raise CliArgumentError(f"unsupported go token in transcript: {token}")

    return UsiThinkRequest(
        movetime=values.get("movetime"),
        btime=values.get("btime"),
        wtime=values.get("wtime"),
        binc=values.get("binc"),
        winc=values.get("winc"),
        byoyomi=values.get("byoyomi"),
        depth=values.get("depth"),
        nodes=values.get("nodes"),
        is_infinite=is_infinite,
        is_ponder=is_ponder,
        searchmoves=tuple(searchmoves),
    )


def _target_request_from_args(args: argparse.Namespace, *, fallback: UsiThinkRequest | None) -> UsiThinkRequest:
    try:
        searchmoves = tuple(Move.from_usi(m) for m in (args.searchmoves or ()))
    except ValueError as exc:
        raise CliArgumentError(f"invalid --searchmoves USI move: {exc}") from exc
    if getattr(args, "infinite", False) and args.timeout is None:
        raise CliArgumentError("--infinite requires --timeout so the search can be stopped")
    has_explicit = any(
        value is not None
        for value in (
            args.nodes,
            args.depth,
            args.movetime,
        )
    ) or bool(args.searchmoves)
    if not has_explicit and not getattr(args, "infinite", False):
        if fallback is not None:
            return fallback
        raise CliArgumentError("specify --nodes, --depth, --movetime, or enable tournament transcripts")
    if args.movetime is not None and args.movetime <= 0:
        raise CliArgumentError("--movetime must be > 0")
    if args.nodes is not None and args.nodes <= 0:
        raise CliArgumentError("--nodes must be > 0")
    if args.depth is not None and args.depth <= 0:
        raise CliArgumentError("--depth must be > 0")
    return UsiThinkRequest(
        movetime=args.movetime,
        depth=args.depth,
        nodes=args.nodes,
        is_infinite=bool(getattr(args, "infinite", False)),
        searchmoves=searchmoves,
    )


def _parse_engine_option_overrides(option_list: list[str] | None) -> JsonObject:
    result: JsonObject = {}
    for key, value in parse_option_overrides(option_list).items():
        result[key] = value
    return result


def _resolve_engine(
    *,
    run_dir: Path,
    engine_argument: str,
    option_overrides: JsonObject,
) -> ResolvedEngine:
    candidate = Path(engine_argument).expanduser()
    if candidate.exists():
        return ResolvedEngine(
            label=engine_argument,
            engine_argument=str(candidate.resolve()),
            extra_options=option_overrides,
        )

    manifest = read_run_manifest(run_dir / "manifest.json")
    engines = manifest.get("engines") if manifest is not None else None
    if isinstance(engines, list):
        for raw_engine in engines:
            if not isinstance(raw_engine, Mapping):
                continue
            engine = {str(key): json_serialize(value) for key, value in raw_engine.items()}
            if engine_argument not in _manifest_engine_aliases(engine):
                continue
            resolved_paths = _object_or_empty(engine.get("resolved_paths"))
            engine_config = coerce_str(resolved_paths.get("engine_config"))
            if engine_config:
                return ResolvedEngine(
                    label=engine_argument,
                    engine_argument=engine_config,
                    extra_options=option_overrides,
                )
            engine_path = coerce_str(resolved_paths.get("engine_path"))
            if engine_path:
                effective_options = _object_or_empty(engine.get("effective_options"))
                merged_options = dict(effective_options)
                merged_options.update(option_overrides)
                return ResolvedEngine(
                    label=engine_argument,
                    engine_argument=engine_path,
                    extra_options=merged_options,
                )

    raise CliArgumentError(f"engine not found as path or manifest entry: {engine_argument}")


def _manifest_engine_aliases(engine: JsonObject) -> set[str]:
    aliases: set[str] = set()
    for key in ("name", "artifact"):
        value = coerce_str(engine.get(key))
        if value:
            aliases.add(value)
    return aliases


async def _run_replay_for_engine(
    *,
    args: argparse.Namespace,
    target: ReplayTarget,
    mode: ReplayMode,
    traces: Sequence[SearchTrace],
    request: UsiThinkRequest,
    engine: ResolvedEngine,
    run_dir: Path,
) -> ReplaySearchSummary:
    if request.is_ponder:
        raise CliArgumentError("target replay does not support a ponder go command; pass --nodes/--depth/--movetime")
    runtime = await load_engine(
        engine.engine_argument,
        extra_options=engine.extra_options,
        engine_name=engine.label,
        instance_id=None,
        instance_pool=None,
    )

    transcript_writer: _ReplayTranscriptWriter | None = None
    remove_handler = None
    transcript_path: Path | None = None
    if args.emit_transcript:
        transcript_path = _replay_transcript_path(run_dir, target=target, engine_label=engine.label)
        transcript_writer = _ReplayTranscriptWriter(transcript_path)
        remove_handler = runtime.register_io_log_handler(transcript_writer.handle)

    replayed_searches = 0
    skipped_ponder_searches = 0
    try:
        async with runtime:
            await runtime.new_game()
            if mode == "replay_history":
                if not traces:
                    raise CliArgumentError("history replay requires per-game transcripts for the target side")
                for trace in traces:
                    if len(trace.moves) >= len(target.moves_before):
                        break
                    if trace.request.is_ponder:
                        skipped_ponder_searches += 1
                        continue
                    await runtime.think(
                        sfen=trace.sfen,
                        moves=trace.moves,
                        request=trace.request,
                        timeout=args.timeout,
                    )
                    replayed_searches += 1
            if request.is_infinite:
                # `go infinite` never returns a bestmove on its own; wait up to
                # --timeout, then send `stop` to recover the engine's bestmove.
                try:
                    result = await runtime.think(
                        sfen=target.sfen,
                        moves=target.moves_before,
                        request=request,
                        timeout=args.timeout,
                    )
                except TimeoutError:
                    result = await runtime.stop()
                    if result is None:
                        raise CliError("engine did not return a bestmove after stop") from None
            else:
                result = await runtime.think(
                    sfen=target.sfen,
                    moves=target.moves_before,
                    request=request,
                    timeout=args.timeout,
                )
            if hasattr(runtime, "flush_io_log_handlers"):
                await runtime.flush_io_log_handlers()
    finally:
        if remove_handler is not None:
            remove_handler()
        if transcript_writer is not None:
            transcript_writer.close()

    return _summarize_result(
        engine_label=engine.label,
        mode=mode,
        request=request,
        target=target,
        result=result,
        transcript_path=transcript_path,
        replayed_searches=replayed_searches,
        skipped_ponder_searches=skipped_ponder_searches,
    )


def _summarize_result(
    *,
    engine_label: str,
    mode: ReplayMode,
    request: UsiThinkRequest,
    target: ReplayTarget,
    result: UsiThinkResultPort,
    transcript_path: Path | None,
    replayed_searches: int,
    skipped_ponder_searches: int,
) -> ReplaySearchSummary:
    bestmove = result.bestmove.to_usi() if result.bestmove is not None else "(none)"
    saved_move = target.saved_move.to_usi() if target.saved_move is not None else None
    pv = result.get_last_pv()
    pv_moves = getattr(pv, "pv", None) if pv is not None else None
    score = _format_score(getattr(pv, "eval", None)) if pv is not None else None
    score_type = _format_score_type(getattr(pv, "bound", None)) if pv is not None else None
    return ReplaySearchSummary(
        engine=engine_label,
        mode=mode,
        go_command=request.to_command(),
        bestmove=bestmove,
        saved_move=saved_move,
        is_match=None if saved_move is None else bestmove == saved_move,
        score=score,
        score_type=score_type,
        depth=_optional_int(getattr(pv, "depth", None)) if pv is not None else None,
        seldepth=_optional_int(getattr(pv, "seldepth", None)) if pv is not None else None,
        nodes=_optional_int(getattr(pv, "nodes", None)) if pv is not None else None,
        nps=_optional_int(getattr(pv, "nps", None)) if pv is not None else None,
        hashfull=_optional_int(getattr(pv, "hashfull", None)) if pv is not None else None,
        pv=" ".join(move.to_usi() for move in pv_moves) if isinstance(pv_moves, list | tuple) else None,
        transcript_path=transcript_path,
        replayed_searches=replayed_searches,
        skipped_ponder_searches=skipped_ponder_searches,
    )


def _print_replay_report(*, target: ReplayTarget, summaries: Sequence[ReplaySearchSummary]) -> None:
    print(f"game_id: {target.game_id}")
    print(f"ply: {target.ply}")
    print(f"initial_sfen: {target.initial_sfen}")
    print(f"target_side: {target.role}")
    print(f"black: {target.black_player}")
    print(f"white: {target.white_player}")
    print(f"position: {target.position_command}")
    if target.saved_move is not None:
        print(f"saved_move: {target.saved_move.to_usi()}")
    for index, summary in enumerate(summaries):
        if len(summaries) > 1:
            print("")
            print(f"[{index + 1}] {summary.engine}")
        print(f"engine: {summary.engine}")
        print(f"mode: {summary.mode}")
        print(f"go: {summary.go_command}")
        print(f"bestmove: {summary.bestmove}")
        if summary.score is not None:
            print(f"score: {summary.score}")
        if summary.score_type is not None:
            print(f"score_type: {summary.score_type}")
        _print_optional_int("depth", summary.depth)
        _print_optional_int("seldepth", summary.seldepth)
        _print_optional_int("nodes", summary.nodes)
        _print_optional_int("nps", summary.nps)
        _print_optional_int("hashfull", summary.hashfull)
        if summary.pv:
            print(f"pv: {summary.pv}")
        if summary.is_match is not None:
            print(f"matches_saved_move: {str(summary.is_match).lower()}")
        if summary.replayed_searches or summary.skipped_ponder_searches:
            print(f"replayed_searches: {summary.replayed_searches}")
            print(f"skipped_ponder_searches: {summary.skipped_ponder_searches}")
        if summary.transcript_path is not None:
            print(f"transcript: {summary.transcript_path}")
    if len(summaries) == 2:
        print("")
        print(f"bestmove_equal: {str(summaries[0].bestmove == summaries[1].bestmove).lower()}")


def _replay_transcript_path(run_dir: Path, *, target: ReplayTarget, engine_label: str) -> Path:
    safe_game = safe_transcript_game_id(target.game_id)
    safe_engine = safe_transcript_game_id(engine_label)
    return run_dir / "replay-transcripts" / f"game-{safe_game}-ply-{target.ply:04d}-{safe_engine}.log"


def _format_score(eval_value: object) -> str | None:
    if eval_value is None:
        return None
    formatter = getattr(eval_value, "to_string", None)
    if callable(formatter):
        formatted = formatter()
        if isinstance(formatted, str):
            return formatted
    if isinstance(eval_value, int):
        return f"cp {eval_value}"
    return str(eval_value)


def _format_score_type(bound_value: object) -> str | None:
    formatter = getattr(bound_value, "to_string", None)
    if callable(formatter):
        formatted = formatter()
        if isinstance(formatted, str) and formatted:
            return formatted
    return "exact" if bound_value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _print_optional_int(label: str, value: int | None) -> None:
    if value is not None:
        print(f"{label}: {value}")


def _object_or_empty(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): json_serialize(item) for key, item in value.items()}


__all__ = ["parse_go_command", "register"]
