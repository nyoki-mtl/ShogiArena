"""Fold CSA bridge records into run state.

``fold`` is total: every input produces a state. Unknown record types and gaps in
``seq`` are counted and stepped over, because half a run is still worth watching.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from shogiarena._core.contexts.csa_watch.domain.event_records import (
    AlertPayload,
    BridgeStartPayload,
    EngineMetaPayload,
    GameEndPayload,
    GameStartPayload,
    GoPayload,
    InvalidLine,
    KnownRecord,
    LivenessPayload,
    MalformedRecord,
    MovePayload,
    ParsedRecord,
    PonderPayload,
    RecordEnvelope,
    StatePayload,
    UnknownRecord,
)
from shogiarena._core.contexts.csa_watch.domain.run_state import (
    PONDER_HIT,
    PONDER_MISS,
    PONDER_STARTED,
    GameState,
    PendingSearch,
    RecordedMove,
    RunState,
    alert_from,
    replayed_moves,
)


def _account_for_seq(state: RunState, envelope: RecordEnvelope) -> RunState:
    """Count gaps in ``seq`` without trying to repair them."""
    missing = 0
    if state.last_seq is not None and envelope.seq > state.last_seq + 1:
        missing = envelope.seq - state.last_seq - 1
    health = state.health if missing == 0 else replace(state.health, missing_seq=state.health.missing_seq + missing)
    return replace(state, last_seq=envelope.seq, last_ts=envelope.ts or state.last_ts, health=health)


def _fold_bridge_start(state: RunState, payload: BridgeStartPayload) -> RunState:
    return replace(state, version=payload.version, engine_cmd=payload.engine_cmd)


def _fold_engine_meta(state: RunState, payload: EngineMetaPayload) -> RunState:
    return replace(
        state,
        engine_name=payload.name,
        engine_author=payload.author,
        engine_options=payload.options,
    )


def _fold_state_change(state: RunState, payload: StatePayload, envelope: RecordEnvelope) -> RunState:
    if state.phase == payload.phase:
        return state
    return replace(state, phase=payload.phase, phase_since_ts=envelope.ts)


def _fold_alert(state: RunState, payload: AlertPayload, envelope: RecordEnvelope) -> RunState:
    entry = alert_from(payload, seq=envelope.seq, ts=envelope.ts, game_id=envelope.game_id)
    return replace(state, alerts=(*state.alerts, entry))


def _fold_game_start(state: RunState, payload: GameStartPayload, envelope: RecordEnvelope) -> RunState:
    game_id = envelope.game_id
    if not game_id:
        return replace(state, health=replace(state.health, orphan_records=state.health.orphan_records + 1))

    game = GameState(
        game_id=game_id,
        black_name=payload.black_name,
        white_name=payload.white_name,
        my_color=payload.my_color,
        initial_sfen=payload.initial_sfen,
        time=payload.time,
        entering_king_rule=payload.entering_king_rule,
        max_moves=payload.max_moves,
        moves=replayed_moves(payload.initial_sfen, payload.moves_so_far),
        started_ts=envelope.ts,
    )
    return replace(state.with_game(game), current_game_id=game_id)


def _target_game(state: RunState, envelope: RecordEnvelope) -> GameState | None:
    if envelope.game_id:
        return state.game_by_id(envelope.game_id)
    return state.current_game


def _fold_move(state: RunState, payload: MovePayload, envelope: RecordEnvelope) -> RunState:
    game = _target_game(state, envelope)
    if game is None:
        return replace(state, health=replace(state.health, orphan_records=state.health.orphan_records + 1))

    move = RecordedMove.from_payload(payload, ts=envelope.ts)
    # A move that does not continue the list means an earlier `move` record never
    # reached the file. The per-ply arrays index by position, so the drift has to
    # be visible rather than silently shifting every later evaluation by one.
    if move.ply > len(game.moves) + 1:
        state = replace(state, health=replace(state.health, ply_gaps=state.health.ply_gaps + 1))
    moves = _append_or_replace_move(game, move)
    updated = replace(
        game,
        moves=moves,
        black_remaining_ms=payload.black_remaining_ms
        if payload.black_remaining_ms is not None
        else game.black_remaining_ms,
        white_remaining_ms=payload.white_remaining_ms
        if payload.white_remaining_ms is not None
        else game.white_remaining_ms,
        ledger_as_of_ts=envelope.ts if envelope.ts is not None else game.ledger_as_of_ts,
        pending=None,
    )
    return state.with_game(updated)


def _append_or_replace_move(game: GameState, move: RecordedMove) -> tuple[RecordedMove, ...]:
    """Moves arrive in order, but ``moves_so_far`` may already cover this ply."""
    if 1 <= move.ply <= len(game.moves):
        moves = list(game.moves)
        moves[move.ply - 1] = move
        return tuple(moves)
    return (*game.moves, move)


def _fold_go(state: RunState, payload: GoPayload, envelope: RecordEnvelope) -> RunState:
    game = _target_game(state, envelope)
    if game is None:
        return replace(state, health=replace(state.health, orphan_records=state.health.orphan_records + 1))
    pending = PendingSearch(
        kind="go",
        ply=payload.ply,
        deadline_ts=envelope.wall_clock_for(payload.deadline_at),
        started_ts=envelope.ts,
    )
    return state.with_game(replace(game, pending=pending))


def _fold_ponder(state: RunState, payload: PonderPayload, envelope: RecordEnvelope) -> RunState:
    game = _target_game(state, envelope)
    if game is None:
        # A ponder record for a game we never saw start is not evidence about
        # prediction quality; counting it in the tally would inflate the rate.
        return replace(state, health=replace(state.health, orphan_records=state.health.orphan_records + 1))

    tally = state.ponder
    if payload.outcome == PONDER_STARTED:
        tally = replace(tally, started=tally.started + 1)
    elif payload.outcome == PONDER_HIT:
        tally = replace(tally, hits=tally.hits + 1)
    elif payload.outcome == PONDER_MISS:
        tally = replace(tally, misses=tally.misses + 1)
    state = replace(state, ponder=tally)

    if payload.outcome == PONDER_STARTED:
        pending = PendingSearch(
            kind="ponder",
            ply=payload.ply,
            deadline_ts=envelope.wall_clock_for(payload.deadline_at),
            started_ts=envelope.ts,
            predicted_usi=payload.predicted_usi,
        )
    elif payload.outcome == PONDER_HIT:
        # A hit turns the speculative search into the real one; no ``go`` follows.
        pending = PendingSearch(
            kind="ponderhit",
            ply=payload.ply,
            deadline_ts=envelope.wall_clock_for(payload.deadline_at),
            started_ts=envelope.ts,
            predicted_usi=payload.predicted_usi,
        )
    elif payload.outcome == PONDER_MISS:
        # A miss is followed by a ``go`` that restates the same deadline.
        pending = PendingSearch(
            kind="go",
            ply=payload.ply,
            deadline_ts=envelope.wall_clock_for(payload.deadline_at),
            started_ts=envelope.ts,
        )
    else:
        # An outcome this build does not know: leave the search state alone.
        return state
    return state.with_game(replace(game, pending=pending))


def _fold_game_end(state: RunState, payload: GameEndPayload, envelope: RecordEnvelope) -> RunState:
    game = _target_game(state, envelope)
    if game is None:
        return replace(state, health=replace(state.health, orphan_records=state.health.orphan_records + 1))
    updated = replace(
        game,
        result=payload.result or "unknown",
        terminal=payload.terminal,
        black_remaining_ms=payload.black_remaining_ms
        if payload.black_remaining_ms is not None
        else game.black_remaining_ms,
        white_remaining_ms=payload.white_remaining_ms
        if payload.white_remaining_ms is not None
        else game.white_remaining_ms,
        ledger_as_of_ts=envelope.ts if envelope.ts is not None else game.ledger_as_of_ts,
        pending=None,
        ended_ts=envelope.ts,
    )
    return replace(state.with_game(updated), current_game_id=None)


def _fold_known(state: RunState, record: KnownRecord) -> RunState:
    payload = record.payload
    envelope = record.envelope
    if isinstance(payload, EngineMetaPayload):
        return _fold_engine_meta(state, payload)
    if isinstance(payload, LivenessPayload):
        # The record's arrival is the whole message. Envelope accounting has
        # already moved `last_ts` forward, which is exactly what keeps a live
        # bridge from looking silent.
        return replace(state, emits_liveness=True, stopped=state.stopped or payload.is_stop)
    if isinstance(payload, BridgeStartPayload):
        return _fold_bridge_start(state, payload)
    if isinstance(payload, StatePayload):
        return _fold_state_change(state, payload, envelope)
    if isinstance(payload, AlertPayload):
        return _fold_alert(state, payload, envelope)
    if isinstance(payload, GameStartPayload):
        return _fold_game_start(state, payload, envelope)
    if isinstance(payload, MovePayload):
        return _fold_move(state, payload, envelope)
    if isinstance(payload, GoPayload):
        return _fold_go(state, payload, envelope)
    if isinstance(payload, PonderPayload):
        return _fold_ponder(state, payload, envelope)
    return _fold_game_end(state, payload, envelope)


def fold(state: RunState, record: ParsedRecord) -> RunState:
    """Apply one parsed record. Never raises, never drops the state it was given."""
    if isinstance(record, InvalidLine):
        return replace(state, health=replace(state.health, invalid_lines=state.health.invalid_lines + 1))
    if isinstance(record, UnknownRecord):
        state = _account_for_seq(state, record.envelope)
        return replace(state, health=state.health.with_unknown(record.envelope.type))
    if isinstance(record, MalformedRecord):
        state = _account_for_seq(state, record.envelope)
        return replace(state, health=replace(state.health, malformed=state.health.malformed + 1))
    state = _account_for_seq(state, record.envelope)
    return _fold_known(state, record)


def fold_all(state: RunState, records: Iterable[ParsedRecord]) -> RunState:
    """Fold a sequence of records left to right."""
    for record in records:
        state = fold(state, record)
    return state


__all__ = ["fold", "fold_all"]
