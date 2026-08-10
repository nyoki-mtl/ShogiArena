"""Record model for the ``{run_id}-events.jsonl`` log written by ``rsshogi-csa-bridge``.

The contract is append-only: field meanings and types never change, but new record
types and new fields appear without notice. The reader therefore keeps the envelope
strict and the body tolerant, and separates two very different situations:

* an unknown ``type`` is expected evolution and must never stop the fold;
* a known ``type`` whose payload does not parse is a defect worth reporting.

See ``agent-docs/architecture/csa-event-log-contract.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.exceptions import ContractParseError

CsaColor = Literal["black", "white"]

BRIDGE_START = "bridge_start"
GAME_START = "game_start"
MOVE = "move"
GO = "go"
PONDER = "ponder"
GAME_END = "game_end"
STATE = "state"
ALERT = "alert"
# Liveness records. A bridge killed mid-game leaves no trace of its death, so its
# run sits in `playing` for ever and a reader cannot tell it from a live game.
# `heartbeat` fills the silence — a live bridge writes something at least every
# 60 seconds — and `bridge_stop` marks a log as deliberately complete.
#
# Recognised here *before* the bridge emits them. The other order would surface
# them as "unknown type" damage on every healthy run, turning the log-health
# signal into a permanent false alarm.
HEARTBEAT = "heartbeat"
BRIDGE_STOP = "bridge_stop"
ENGINE_META = "engine_meta"

FALLBACK_ORIGIN_PREFIX = "fallback"

_BOUNDARY_ID = "BND-CSA-EVENT-LOG"


class _EnvelopeModel(BaseModel):
    """Strict part of a record. A line missing any of these is not a record at all."""

    model_config = ConfigDict(extra="ignore")

    seq: int
    at: int
    type: str = Field(min_length=1)
    ts: int | None = None
    game_id: str | None = None
    schema_version: int | None = Field(default=None, alias="schema")


class _NamesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    black: str = ""
    white: str = ""


class _TimeModel(BaseModel):
    """``game_start.time``. Every field is optional so that additions stay harmless."""

    model_config = ConfigDict(extra="ignore")

    total_ms: int | None = None
    byoyomi_ms: int | None = None
    inc_ms: int | None = None
    least_ms: int | None = None
    unit_ms: int | None = None
    roundup: bool | None = None


class _GoTimesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    btime_ms: int | None = None
    wtime_ms: int | None = None
    byoyomi_ms: int | None = None
    binc_ms: int | None = None
    winc_ms: int | None = None


class _EvalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cp: int | None = None
    mate: int | None = None
    depth: int | None = None
    nodes: int | None = None
    pv_usi: list[str] = Field(default_factory=list)


class _BridgeStartModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str
    engine_cmd: str


class _GameStartModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    names: _NamesModel
    my_color: CsaColor
    initial_sfen: str = Field(min_length=1)
    moves_so_far: list[str] = Field(default_factory=list)
    time: _TimeModel = Field(default_factory=_TimeModel)
    entering_king_rule: str | None = None
    max_moves: int | None = None


class _MoveModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ply: int
    side: str
    usi: str = Field(min_length=1)
    csa: str | None = None
    t_ms: int | None = None
    black_remaining_ms: int | None = None
    white_remaining_ms: int | None = None
    eval: _EvalModel | None = None
    by: str = ""


class _GoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ply: int
    times: _GoTimesModel = Field(default_factory=_GoTimesModel)
    deadline_at: int | None = None


class _PonderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outcome: str
    ply: int
    predicted_usi: str | None = None
    actual_usi: str | None = None
    deadline_at: int | None = None


class _GameEndModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: str = ""
    terminal: list[str] = Field(default_factory=list)
    black_remaining_ms: int | None = None
    white_remaining_ms: int | None = None


class _EngineOptionModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    value: str = ""


class _EngineMetaModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    author: str | None = None
    options: list[_EngineOptionModel] = Field(default_factory=list)


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase: str = Field(min_length=1)


class _AlertModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: str = Field(min_length=1)
    code: str = Field(min_length=1)
    detail: str | None = None


@dataclass(frozen=True)
class RecordEnvelope:
    """Identity and ordering of a record, independent of its type."""

    seq: int
    at: int
    type: str
    ts: int | None = None
    game_id: str | None = None
    schema_version: int | None = None

    def wall_clock_for(self, monotonic_ms: int | None) -> int | None:
        """Map a bridge-local monotonic stamp onto the writer's wall clock.

        ``deadline_at`` only means something inside the bridge process, so the
        offset is taken from the record that carries it and never shared across
        the run: a restarted process gets a different origin.
        """
        if monotonic_ms is None or self.ts is None:
            return None
        return monotonic_ms + (self.ts - self.at)


@dataclass(frozen=True)
class EvalPayload:
    cp: int | None
    mate: int | None
    depth: int | None
    nodes: int | None
    pv_usi: tuple[str, ...]


@dataclass(frozen=True)
class TimeControlPayload:
    total_ms: int | None
    byoyomi_ms: int | None
    inc_ms: int | None
    least_ms: int | None
    unit_ms: int | None
    roundup: bool | None


@dataclass(frozen=True)
class BridgeStartPayload:
    version: str
    engine_cmd: str


@dataclass(frozen=True)
class GameStartPayload:
    black_name: str
    white_name: str
    my_color: CsaColor
    initial_sfen: str
    moves_so_far: tuple[str, ...]
    time: TimeControlPayload
    entering_king_rule: str | None
    max_moves: int | None


@dataclass(frozen=True)
class MovePayload:
    ply: int
    side: str
    usi: str
    csa: str | None
    t_ms: int | None
    black_remaining_ms: int | None
    white_remaining_ms: int | None
    eval: EvalPayload | None
    by: str

    @property
    def is_fallback(self) -> bool:
        return self.by.startswith(FALLBACK_ORIGIN_PREFIX)


@dataclass(frozen=True)
class GoPayload:
    ply: int
    btime_ms: int | None
    wtime_ms: int | None
    byoyomi_ms: int | None
    binc_ms: int | None
    winc_ms: int | None
    deadline_at: int | None


@dataclass(frozen=True)
class PonderPayload:
    outcome: str
    ply: int
    predicted_usi: str | None
    actual_usi: str | None
    deadline_at: int | None


@dataclass(frozen=True)
class GameEndPayload:
    result: str
    terminal: tuple[str, ...]
    black_remaining_ms: int | None
    white_remaining_ms: int | None


@dataclass(frozen=True)
class StatePayload:
    phase: str


@dataclass(frozen=True)
class AlertPayload:
    level: str
    code: str
    detail: str | None

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass(frozen=True)
class EngineMetaPayload:
    """What the engine turned out to be, once the USI handshake said so.

    Distinct from `bridge_start`, which states how the *bridge* was configured at
    spawn. The version in `bridge_start` is the bridge's, and reading it as the
    engine's was the confusion this record exists to end.
    """

    name: str | None
    author: str | None
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LivenessPayload:
    """A `heartbeat` or `bridge_stop`.

    Deliberately empty. The envelope already carries the timestamp, which is the
    entire content of "the bridge was alive at this moment" and of "this log ends
    here on purpose". Copying the phase in would create a second, staler answer to
    a question `state` already answers.
    """

    is_stop: bool


RecordPayload = (
    BridgeStartPayload
    | GameStartPayload
    | MovePayload
    | GoPayload
    | PonderPayload
    | GameEndPayload
    | StatePayload
    | AlertPayload
    | LivenessPayload
    | EngineMetaPayload
)


@dataclass(frozen=True)
class KnownRecord:
    """A record this build understands."""

    envelope: RecordEnvelope
    payload: RecordPayload


@dataclass(frozen=True)
class UnknownRecord:
    """A record type this build has never heard of. Expected, counted, not fatal."""

    envelope: RecordEnvelope


@dataclass(frozen=True)
class MalformedRecord:
    """A known record type whose payload did not parse. A defect signal."""

    envelope: RecordEnvelope
    reason: str


@dataclass(frozen=True)
class InvalidLine:
    """A line that carries no usable envelope, so it is not a record at all."""

    reason: str


ParsedRecord = KnownRecord | UnknownRecord | MalformedRecord | InvalidLine


def _eval_from(model: _EvalModel | None) -> EvalPayload | None:
    if model is None:
        return None
    return EvalPayload(
        cp=model.cp,
        mate=model.mate,
        depth=model.depth,
        nodes=model.nodes,
        pv_usi=tuple(model.pv_usi),
    )


def _build_payload(record_type: str, body: Mapping[str, object]) -> RecordPayload:
    if record_type == BRIDGE_START:
        parsed = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_BridgeStartModel, path=record_type)
        return BridgeStartPayload(version=parsed.version, engine_cmd=parsed.engine_cmd)
    if record_type == GAME_START:
        start = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_GameStartModel, path=record_type)
        return GameStartPayload(
            black_name=start.names.black,
            white_name=start.names.white,
            my_color=start.my_color,
            initial_sfen=start.initial_sfen,
            moves_so_far=tuple(start.moves_so_far),
            time=TimeControlPayload(
                total_ms=start.time.total_ms,
                byoyomi_ms=start.time.byoyomi_ms,
                inc_ms=start.time.inc_ms,
                least_ms=start.time.least_ms,
                unit_ms=start.time.unit_ms,
                roundup=start.time.roundup,
            ),
            entering_king_rule=start.entering_king_rule,
            max_moves=start.max_moves,
        )
    if record_type == MOVE:
        move = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_MoveModel, path=record_type)
        return MovePayload(
            ply=move.ply,
            side=move.side,
            usi=move.usi,
            csa=move.csa,
            t_ms=move.t_ms,
            black_remaining_ms=move.black_remaining_ms,
            white_remaining_ms=move.white_remaining_ms,
            eval=_eval_from(move.eval),
            by=move.by,
        )
    if record_type == GO:
        go = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_GoModel, path=record_type)
        return GoPayload(
            ply=go.ply,
            btime_ms=go.times.btime_ms,
            wtime_ms=go.times.wtime_ms,
            byoyomi_ms=go.times.byoyomi_ms,
            binc_ms=go.times.binc_ms,
            winc_ms=go.times.winc_ms,
            deadline_at=go.deadline_at,
        )
    if record_type == PONDER:
        ponder = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_PonderModel, path=record_type)
        return PonderPayload(
            outcome=ponder.outcome,
            ply=ponder.ply,
            predicted_usi=ponder.predicted_usi,
            actual_usi=ponder.actual_usi,
            deadline_at=ponder.deadline_at,
        )
    if record_type == GAME_END:
        end = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_GameEndModel, path=record_type)
        return GameEndPayload(
            result=end.result,
            terminal=tuple(end.terminal),
            black_remaining_ms=end.black_remaining_ms,
            white_remaining_ms=end.white_remaining_ms,
        )
    if record_type == ENGINE_META:
        meta = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_EngineMetaModel, path=record_type)
        return EngineMetaPayload(
            name=meta.name,
            author=meta.author,
            options=tuple((option.name, option.value) for option in meta.options),
        )
    if record_type in _BODYLESS_RECORD_TYPES:
        return LivenessPayload(is_stop=record_type == BRIDGE_STOP)
    if record_type == STATE:
        state = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_StateModel, path=record_type)
        return StatePayload(phase=state.phase)
    alert = parse_wire(boundary_id=_BOUNDARY_ID, payload=body, model=_AlertModel, path=record_type)
    return AlertPayload(level=alert.level, code=alert.code, detail=alert.detail)


KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        BRIDGE_START,
        GAME_START,
        MOVE,
        GO,
        PONDER,
        GAME_END,
        STATE,
        ALERT,
        HEARTBEAT,
        BRIDGE_STOP,
        ENGINE_META,
    }
)

# Liveness records carry no body; the envelope's timestamp is the whole message.
_BODYLESS_RECORD_TYPES: frozenset[str] = frozenset({HEARTBEAT, BRIDGE_STOP})


def parse_event_record(payload: Mapping[str, object]) -> ParsedRecord:
    """Classify one decoded JSONL object into a record, or explain why it is not one."""
    try:
        envelope_model = parse_wire(boundary_id=_BOUNDARY_ID, payload=payload, model=_EnvelopeModel)
    except ContractParseError:
        return InvalidLine(reason="missing or invalid envelope (seq/at/type)")

    envelope = RecordEnvelope(
        seq=envelope_model.seq,
        at=envelope_model.at,
        type=envelope_model.type,
        ts=envelope_model.ts,
        game_id=envelope_model.game_id,
        schema_version=envelope_model.schema_version,
    )
    if envelope.type not in KNOWN_RECORD_TYPES:
        return UnknownRecord(envelope=envelope)
    try:
        return KnownRecord(envelope=envelope, payload=_build_payload(envelope.type, payload))
    except ContractParseError as exc:
        return MalformedRecord(envelope=envelope, reason=str(exc))


__all__ = [
    "ALERT",
    "BRIDGE_START",
    "BRIDGE_STOP",
    "ENGINE_META",
    "GAME_END",
    "GAME_START",
    "GO",
    "HEARTBEAT",
    "KNOWN_RECORD_TYPES",
    "MOVE",
    "PONDER",
    "STATE",
    "AlertPayload",
    "BridgeStartPayload",
    "CsaColor",
    "EvalPayload",
    "GameEndPayload",
    "GameStartPayload",
    "GoPayload",
    "InvalidLine",
    "KnownRecord",
    "EngineMetaPayload",
    "LivenessPayload",
    "MalformedRecord",
    "MovePayload",
    "ParsedRecord",
    "PonderPayload",
    "RecordEnvelope",
    "RecordPayload",
    "StatePayload",
    "TimeControlPayload",
    "UnknownRecord",
    "parse_event_record",
]
