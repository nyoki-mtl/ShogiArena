"""State a CSA bridge run is folded into.

Everything here is immutable and free of I/O: the fold is a pure function of the
record stream, so a half-read log and a complete log go through the same code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from shogiarena._core.contexts.csa_watch.domain.event_records import (
    FALLBACK_ORIGIN_PREFIX,
    AlertPayload,
    CsaColor,
    EvalPayload,
    MovePayload,
    TimeControlPayload,
)

SearchKind = Literal["go", "ponder", "ponderhit"]

PONDER_STARTED = "started"
PONDER_HIT = "hit"
PONDER_MISS = "miss"

REPLAYED_ORIGIN = "replayed"

HIRATE_SFEN = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"

_SFEN_BLACK_TO_MOVE = "b"


def side_to_move_of(initial_sfen: str) -> CsaColor:
    """Read the side to move out of an SFEN. Falls back to black for junk input."""
    parts = initial_sfen.split()
    if len(parts) >= 2 and parts[1] != _SFEN_BLACK_TO_MOVE:
        return "white"
    return "black"


@dataclass(frozen=True)
class RecordedMove:
    """One played move as the log described it."""

    ply: int
    side: str
    usi: str
    csa: str | None = None
    t_ms: int | None = None
    eval: EvalPayload | None = None
    by: str = ""
    ts: int | None = None

    @property
    def is_fallback(self) -> bool:
        return self.by.startswith(FALLBACK_ORIGIN_PREFIX)

    @classmethod
    def from_payload(cls, payload: MovePayload, *, ts: int | None) -> RecordedMove:
        return cls(
            ply=payload.ply,
            side=payload.side,
            usi=payload.usi,
            csa=payload.csa,
            t_ms=payload.t_ms,
            eval=payload.eval,
            by=payload.by,
            ts=ts,
        )


@dataclass(frozen=True)
class PendingSearch:
    """A search the engine has been asked for and has not answered yet."""

    kind: SearchKind
    ply: int
    deadline_ts: int | None = None
    started_ts: int | None = None
    predicted_usi: str | None = None


@dataclass(frozen=True)
class PonderTally:
    """Ponder outcomes for a run. ``started`` counts predictions, not searches."""

    started: int = 0
    hits: int = 0
    misses: int = 0

    @property
    def resolved(self) -> int:
        return self.hits + self.misses

    @property
    def unresolved(self) -> int:
        return max(0, self.started - self.resolved)

    @property
    def hit_rate(self) -> float | None:
        if self.resolved == 0:
            return None
        return self.hits / self.resolved


@dataclass(frozen=True)
class AlertEntry:
    """One alert, kept in arrival order with the identity needed to sort it."""

    seq: int
    level: str
    code: str
    detail: str | None = None
    ts: int | None = None
    game_id: str | None = None

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass(frozen=True)
class GameState:
    """One CSA game inside a run."""

    game_id: str
    black_name: str = ""
    white_name: str = ""
    my_color: CsaColor = "black"
    initial_sfen: str = ""
    time: TimeControlPayload | None = None
    entering_king_rule: str | None = None
    max_moves: int | None = None
    moves: tuple[RecordedMove, ...] = ()
    result: str | None = None
    terminal: tuple[str, ...] = ()
    black_remaining_ms: int | None = None
    white_remaining_ms: int | None = None
    ledger_as_of_ts: int | None = None
    pending: PendingSearch | None = None
    started_ts: int | None = None
    ended_ts: int | None = None

    @property
    def is_finished(self) -> bool:
        return self.result is not None

    @property
    def current_ply(self) -> int:
        return len(self.moves)

    @property
    def fallback_plies(self) -> tuple[int, ...]:
        return tuple(move.ply for move in self.moves if move.is_fallback)

    @property
    def opponent_name(self) -> str:
        return self.white_name if self.my_color == "black" else self.black_name

    @property
    def side_to_move(self) -> CsaColor:
        first = side_to_move_of(self.initial_sfen) if self.initial_sfen else "black"
        if self.current_ply % 2 == 0:
            return first
        return "white" if first == "black" else "black"


@dataclass(frozen=True)
class LogHealth:
    """How trustworthy the fold is, kept separate from what it folded."""

    missing_seq: int = 0
    malformed: int = 0
    invalid_lines: int = 0
    orphan_records: int = 0
    ply_gaps: int = 0
    unknown_types: Mapping[str, int] = field(default_factory=dict)

    @property
    def unknown_total(self) -> int:
        return sum(self.unknown_types.values())

    @property
    def is_clean(self) -> bool:
        return (
            self.missing_seq == 0
            and self.malformed == 0
            and self.invalid_lines == 0
            and self.orphan_records == 0
            and self.ply_gaps == 0
            and self.unknown_total == 0
        )

    def with_unknown(self, record_type: str) -> LogHealth:
        counts = dict(self.unknown_types)
        counts[record_type] = counts.get(record_type, 0) + 1
        return replace(self, unknown_types=counts)


@dataclass(frozen=True)
class RunScore:
    """Run record from our own side."""

    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def decided(self) -> int:
        return self.wins + self.losses + self.draws


@dataclass(frozen=True)
class RunState:
    """Everything the fold knows about one bridge run."""

    run_id: str
    version: str | None = None
    engine_cmd: str | None = None
    # What the engine said it was during the USI handshake. `version` above is
    # the *bridge's*; reading it as the engine's was the confusion `engine_meta`
    # exists to end.
    engine_name: str | None = None
    engine_author: str | None = None
    engine_options: tuple[tuple[str, str], ...] = ()
    phase: str | None = None
    phase_since_ts: int | None = None
    games: tuple[GameState, ...] = ()
    current_game_id: str | None = None
    alerts: tuple[AlertEntry, ...] = ()
    ponder: PonderTally = PonderTally()
    health: LogHealth = LogHealth()
    last_seq: int | None = None
    last_ts: int | None = None
    # True once this run has written a liveness record, which is how we learn its
    # bridge is a version that writes them. Inferred from what arrived rather
    # than read from `version`: tying log meaning to a version table would make
    # every reader maintain that table for ever, and the records already
    # describe themselves.
    emits_liveness: bool = False
    # True once the bridge said it was finished. Distinct from `phase ==
    # "closing"`, which is the *start* of shutdown — a bridge killed during
    # shutdown leaves `closing` as its final word.
    stopped: bool = False

    @property
    def current_game(self) -> GameState | None:
        if self.current_game_id is None:
            return None
        for game in reversed(self.games):
            if game.game_id == self.current_game_id:
                return game
        return None

    @property
    def score(self) -> RunScore:
        wins = sum(1 for game in self.games if game.result == "win")
        losses = sum(1 for game in self.games if game.result == "lose")
        draws = sum(1 for game in self.games if game.result == "draw")
        return RunScore(wins=wins, losses=losses, draws=draws)

    def game_by_id(self, game_id: str) -> GameState | None:
        for game in self.games:
            if game.game_id == game_id:
                return game
        return None

    def with_game(self, game: GameState) -> RunState:
        games = list(self.games)
        for index, existing in enumerate(games):
            if existing.game_id == game.game_id:
                games[index] = game
                return replace(self, games=tuple(games))
        games.append(game)
        return replace(self, games=tuple(games))

    def sorted_alerts(self) -> tuple[AlertEntry, ...]:
        """Errors first, newest first within each level."""
        return tuple(sorted(self.alerts, key=lambda entry: (0 if entry.is_error else 1, -entry.seq)))


def replayed_moves(initial_sfen: str, usi_moves: Sequence[str]) -> tuple[RecordedMove, ...]:
    """Turn ``game_start.moves_so_far`` into moves with no engine metadata.

    Buoy openings and operator-resumed games arrive this way: the moves happened,
    but nothing about how they were chosen is recoverable.
    """
    first = side_to_move_of(initial_sfen)
    other: CsaColor = "white" if first == "black" else "black"
    return tuple(
        RecordedMove(
            ply=index + 1,
            side=first if index % 2 == 0 else other,
            usi=usi,
            by=REPLAYED_ORIGIN,
        )
        for index, usi in enumerate(usi_moves)
    )


def alert_from(payload: AlertPayload, *, seq: int, ts: int | None, game_id: str | None) -> AlertEntry:
    return AlertEntry(seq=seq, level=payload.level, code=payload.code, detail=payload.detail, ts=ts, game_id=game_id)


__all__ = [
    "HIRATE_SFEN",
    "PONDER_HIT",
    "PONDER_MISS",
    "PONDER_STARTED",
    "REPLAYED_ORIGIN",
    "AlertEntry",
    "GameState",
    "LogHealth",
    "PendingSearch",
    "PonderTally",
    "RecordedMove",
    "RunScore",
    "RunState",
    "SearchKind",
    "alert_from",
    "replayed_moves",
    "side_to_move_of",
]
