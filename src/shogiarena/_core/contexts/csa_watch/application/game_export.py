"""Rebuild CSA games as records, by replaying them.

Writing the ``.csa`` text directly would be faster and would verify nothing. The
point of this path is not the file: it is that every move in the log has to be
legal on a real board before anything is written. A game that will not replay is
reported and skipped, never written unverified (``0070`` decision 1).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from rsshogi.record import (
    EngineInfo,
    GameResult,
    MoveEntry,
    Record,
    RecordMetadata,
    SpecialMoveEntry,
    TimeControl,
)

from shogiarena._core.contexts.csa_watch.domain.event_records import EvalPayload
from shogiarena._core.contexts.csa_watch.domain.result_mapping import arena_game_result
from shogiarena._core.contexts.csa_watch.domain.run_state import GameState, RecordedMove, RunState
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import (
    CSA_EXPORT_VERSION,
    CSA_FILE_SUFFIX,
    CSA_METADATA_PREFIX,
)
from shogiarena._core.contexts.csa_watch.ports.replay_ports import BoardReplayPort, ReplayResult

logger = logging.getLogger(__name__)

GAME_TYPE = "csa"

# The scale engines use internally, and the one the bridge writes into its own
# records. A mate has no centipawn of its own, and dropping it would lose the
# evaluation at exactly the moves worth looking at.
MATE_SCORE = 32_000
_INT32_MAX = 2**31 - 1
_INT32_MIN = -(2**31)

MAX_PV_MOVES = 32


def mate_to_centipawns(mate: int) -> int:
    """Fold a mate distance onto the centipawn scale, as the bridge does.

    ``i32`` extremes stand for "a win at an unstated distance", which is as strong
    as the scale goes. Every other distance shortens the score the further away
    the mate is.
    """
    if mate in {_INT32_MAX, _INT32_MIN}:
        magnitude = MATE_SCORE
    else:
        magnitude = max(0, MATE_SCORE - abs(mate))
    return magnitude if mate >= 0 else -magnitude


def comment_score(info: EvalPayload) -> int | None:
    if info.cp is not None:
        return info.cp
    if info.mate is None:
        return None
    return mate_to_centipawns(info.mate)


@dataclass(frozen=True)
class ExportedGame:
    """A game that replayed cleanly and is therefore safe to write."""

    game_id: str
    file_name: str
    record: Record


@dataclass(frozen=True)
class ExportFailure:
    """A game that did not replay. Reported, not written."""

    game_id: str
    ply: int
    usi: str
    reason: str


def first_missing_ply(game: GameState) -> int | None:
    """The first ply this game never received, or `None` if it is contiguous.

    Asked per game rather than from the run's `ply_gaps` / `missing_seq`
    counters, which are the wrong key twice over: they would block a clean game
    that merely shares a damaged run, and they cannot say *which* game is short.
    Every move keeps the `ply` the log gave it, so the question is answered
    exactly by asking whether they run 1..n.

    This matters because a gapped kifu does not look broken. The per-ply arrays
    compact around the hole, and two adjacent missing plies leave side-to-move
    parity intact — so the shortened game replays legally and would otherwise be
    written out as an ordinary one.
    """
    for index, move in enumerate(game.moves, start=1):
        if move.ply != index:
            return index
    return None


def _time_control(game: GameState) -> TimeControl | None:
    spec = game.time
    if spec is None:
        return None
    return TimeControl(
        base_seconds=(spec.total_ms or 0) // 1000,
        byoyomi_seconds=(spec.byoyomi_ms or 0) // 1000,
        increment_seconds=(spec.inc_ms or 0) // 1000,
    )


def split_game_id(game_id: str) -> tuple[str | None, str | None, str | None]:
    """``wdoor+floodgate-300-10F+black+white+20260804153004`` -> site, event, start."""
    parts = game_id.split("+")
    if len(parts) < 5:
        return (None, None, None)
    stamp = parts[-1]
    start = None
    if len(stamp) == 14 and stamp.isdigit():
        start = f"{stamp[0:4]}/{stamp[4:6]}/{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}"
    return (parts[0] or None, parts[1] or None, start)


def _csa_stamp(epoch_ms: int | None, *, offset_ms: int) -> str | None:
    """Format a CSA header date on the same clock ``$START_TIME`` already uses."""
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp((epoch_ms + offset_ms) / 1000, tz=UTC).strftime("%Y/%m/%d %H:%M:%S")


def _server_clock_offset_ms(start_date: str | None, started_ts: int | None) -> int:
    """How far the server's stated wall clock runs ahead of epoch time.

    ``$START_TIME`` comes from the game id, which the server writes in its own
    local time; ``ended_ts`` is epoch milliseconds. Formatting one in UTC and
    taking the other verbatim puts the end of the game before its start.
    """
    if start_date is None or started_ts is None:
        return 0
    try:
        stated = datetime.strptime(start_date, "%Y/%m/%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return 0
    return int(stated.timestamp() * 1000) - started_ts


def _iso_stamp(epoch_ms: int | None) -> str:
    """The record store requires ISO-8601; CSA header dates use their own form."""
    moment = datetime.now(tz=UTC) if epoch_ms is None else datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    return moment.isoformat()


def _metadata(game: GameState, *, storage_game_id: str | None = None) -> RecordMetadata:
    site, event, start_date = split_game_id(game.game_id)
    offset_ms = _server_clock_offset_ms(start_date, game.started_ts)
    return RecordMetadata(
        event=event,
        site=site,
        black_player=game.black_name or "Unknown",
        white_player=game.white_name or "Unknown",
        game_name=storage_game_id or game.game_id,
        game_type=GAME_TYPE,
        time_control=_time_control(game),
        max_moves=game.max_moves,
        impasse_rule=game.entering_king_rule,
        start_date=start_date,
        end_date=_csa_stamp(game.ended_ts, offset_ms=offset_ms),
        updated_date=_iso_stamp(game.ended_ts or game.started_ts),
    )


def _pv_in_csa(replayer: BoardReplayPort, sfen_before: str, pv_usi: Sequence[str]) -> list[str]:
    """Convert a principal variation to CSA, stopping where the board stops accepting it.

    A PV is the engine's speculation, not a played line: it can run past a
    position the board will not reach. Truncating is correct; guessing is not.
    """
    if not pv_usi:
        return []
    result = replayer.replay(sfen_before, list(pv_usi)[:MAX_PV_MOVES])
    return [entry.csa for entry in result.plies if entry.csa]


def _move_comment(replayer: BoardReplayPort, move: RecordedMove, sfen_before: str) -> str | None:
    """``* <cp> <pv...> #<nodes>``; the exporter adds the leading ``'*``."""
    info = move.eval
    if info is None:
        return None
    score = comment_score(info)
    if score is None:
        return None
    parts = [f"* {score}"]
    parts.extend(_pv_in_csa(replayer, sfen_before, info.pv_usi))
    if info.nodes is not None:
        parts.append(f"#{info.nodes}")
    return " ".join(parts)


def _engine_info(info: EvalPayload | None) -> EngineInfo | None:
    if info is None:
        return None
    score = comment_score(info)
    if score is None and info.depth is None and info.nodes is None:
        return None
    return EngineInfo(eval=score, depth=info.depth, nodes=info.nodes)


def _terminal_entry(game: GameState, result_name: str | None) -> SpecialMoveEntry | None:
    if result_name is None:
        return None
    raw = next((marker for marker in game.terminal if marker.startswith("%")), None)
    return SpecialMoveEntry.from_result(GameResult.from_str(result_name), raw=raw)


def build_record(
    game: GameState,
    replay: ReplayResult,
    replayer: BoardReplayPort,
    *,
    should_include_comments: bool = True,
    should_attach_engine_info: bool = False,
    storage_game_id: str | None = None,
) -> Record:
    """Assemble the record for a game whose replay already succeeded."""
    entries: list[MoveEntry] = []
    for index, move in enumerate(game.moves):
        played = replay.plies[index]
        # The CSA exporter renders `engine_info` as its own comment line, so a
        # record carrying both prints the evaluation twice. The written comment is
        # the richer form (it carries the principal variation) and belongs in the
        # `.csa`; the structured form is what the database columns are built from.
        comment = _move_comment(replayer, move, played.sfen_before) if should_include_comments else None
        entries.append(
            MoveEntry(
                move.usi,
                time_ms=move.t_ms,
                comment=comment,
                engine_info=_engine_info(move.eval) if should_attach_engine_info else None,
            )
        )
    result_name = arena_game_result(game.result, game.my_color, game.terminal)
    return Record.from_main_line(
        game.initial_sfen,
        entries,
        _terminal_entry(game, result_name),
        _metadata(game, storage_game_id=storage_game_id),
    )


def export_game(
    game: GameState,
    replayer: BoardReplayPort,
    *,
    should_include_comments: bool = True,
    should_attach_engine_info: bool = False,
    storage_game_id: str | None = None,
) -> ExportedGame | ExportFailure:
    """Replay the game, then build its record. Never writes an unverified game.

    Contiguity is checked here rather than at a call site because "never writes
    an unverified game" is this function's promise, and it has two callers: the
    database persister and the `csa export` CLI. Checking it in only the first
    left the second free to write gapped kifu to `.csa` files.
    """
    missing = first_missing_ply(game)
    if missing is not None:
        return ExportFailure(
            game_id=game.game_id,
            ply=missing,
            usi="",
            reason=f"ply {missing} never reached the log; the kifu is not contiguous",
        )
    replay = replayer.replay(game.initial_sfen, [move.usi for move in game.moves])
    if replay.failure is not None:
        return ExportFailure(
            game_id=game.game_id,
            ply=replay.failure.ply,
            usi=replay.failure.usi,
            reason=replay.failure.reason,
        )
    record = build_record(
        game,
        replay,
        replayer,
        should_include_comments=should_include_comments,
        should_attach_engine_info=should_attach_engine_info,
        storage_game_id=storage_game_id,
    )
    return ExportedGame(game_id=game.game_id, file_name=f"{game.game_id}{CSA_FILE_SUFFIX}", record=record)


def export_games(
    games: Sequence[GameState],
    replayer: BoardReplayPort,
    *,
    should_include_comments: bool = True,
) -> tuple[list[ExportedGame], list[ExportFailure]]:
    exported: list[ExportedGame] = []
    failures: list[ExportFailure] = []
    for game in games:
        outcome = export_game(game, replayer, should_include_comments=should_include_comments)
        if isinstance(outcome, ExportFailure):
            failures.append(outcome)
        else:
            exported.append(outcome)
    return exported, failures


def render_csa(record: Record) -> str:
    return record.to_csa(version=CSA_EXPORT_VERSION)


def attach_csa_attributes(record: Record, state: RunState, game: GameState) -> Record:
    """Carry CSA-only facts into the record's metadata attributes.

    Alerts, ponder and move origin have no arena column. Widening ``game.db`` for
    a second conductor would be the wrong trade, so they travel as attributes the
    way timeout attribution already does (``0070`` decision 2).
    """
    ponder = state.ponder
    attributes: dict[str, str] = {
        f"{CSA_METADATA_PREFIX}run_id": state.run_id,
        f"{CSA_METADATA_PREFIX}server_game_id": game.game_id,
        f"{CSA_METADATA_PREFIX}my_color": game.my_color,
        f"{CSA_METADATA_PREFIX}result": game.result or "",
        f"{CSA_METADATA_PREFIX}terminal": " ".join(game.terminal),
        f"{CSA_METADATA_PREFIX}move_origins": json.dumps(
            {str(move.ply): move.by for move in game.moves if move.by not in {"", "engine", "opponent"}},
            ensure_ascii=False,
            sort_keys=True,
        ),
        f"{CSA_METADATA_PREFIX}ponder": json.dumps(
            {"started": ponder.started, "hits": ponder.hits, "misses": ponder.misses},
            sort_keys=True,
        ),
        f"{CSA_METADATA_PREFIX}alerts": json.dumps(
            [
                {"seq": alert.seq, "level": alert.level, "code": alert.code, "detail": alert.detail}
                for alert in state.alerts
                if alert.game_id in {None, game.game_id}
            ],
            ensure_ascii=False,
            sort_keys=True,
        ),
        f"{CSA_METADATA_PREFIX}bridge_version": state.version or "",
    }
    for key, value in attributes.items():
        record.set_metadata_attribute(key, value)
    return record


__all__ = [
    "CSA_EXPORT_VERSION",
    "CSA_FILE_SUFFIX",
    "MATE_SCORE",
    "ExportFailure",
    "attach_csa_attributes",
    "ExportedGame",
    "build_record",
    "comment_score",
    "export_game",
    "export_games",
    "mate_to_centipawns",
    "render_csa",
    "split_game_id",
]
