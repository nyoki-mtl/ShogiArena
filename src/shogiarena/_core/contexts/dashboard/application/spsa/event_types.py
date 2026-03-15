"""SPSA イベントの型定義。

events.jsonl に永続化されるイベントの TypedDict 定義とパーサー。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, ValidationError

from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    OptionalText,
    coerce_int,
    coerce_str,
    coerce_timestamp_ms,
)

from .event_payload_parsers import (
    parse_game_result_payload,
    parse_game_scheduled_payload,
    parse_ltc_regression_result_payload,
    parse_ltc_regression_start_payload,
    parse_update_payload,
    parse_update_pending_payload,
    parse_update_perturbation_payload,
)


class _RawSpsaEventModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: OptionalText = ""
    session_uuid: OptionalText = None
    ts: int | None = None
    update_idx: int | None = None
    family: OptionalText = None
    is_ltc: bool | None = None


# ---------------------------------------------------------------------------
# Common base
# ---------------------------------------------------------------------------


class SpsaEventBase(TypedDict, total=False):
    """全 SPSA イベント共通フィールド。"""

    event: str
    session_uuid: str
    ts: int
    update_idx: int
    family: str
    is_ltc: bool


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class GameScheduledEvent(SpsaEventBase, total=False):
    """``game_scheduled``: 対局がスケジュールされた。"""

    game_id: str
    variant_token: str
    variant_label: str
    tuned_variant_token: str
    baseline_variant_token: str
    phase: str
    is_tuned_as_black: bool
    black_player: str
    white_player: str
    status: str
    assigned_instance: str | None
    worker_idx: int | None
    start_time: str | None


class GameResultEvent(SpsaEventBase, total=False):
    """``game_result``: 対局が完了した。"""

    game_id: str
    winner: int
    is_tuned_as_black: bool
    phase: str
    tuned_variant: str
    baseline_variant: str
    variant_token: str
    variant_label: str
    black_player: str | None
    white_player: str | None
    initial_sfen: str | None
    num_moves: int
    time_control_black: str | None
    time_control_white: str | None
    end_time: str | None
    game_result: str | None


class UpdatePendingEvent(SpsaEventBase, total=False):
    """``update_pending``: パラメータ更新の準備開始。"""

    params: dict[str, float]
    timestamp: int
    perturbations: dict[str, dict[str, float]]
    is_pending: bool
    c_k: float
    a_k: float


class UpdateEvent(SpsaEventBase, total=False):
    """``update``: パラメータ更新が適用された。"""

    params: dict[str, float]
    timestamp: int
    s_plus: float
    s_minus: float
    step: float
    gradients: dict[str, float]
    deltas: dict[str, float]
    delta_norm: float
    batch_size: int
    total_games: int
    perturbations: dict[str, dict[str, float]]
    c_k: float
    a_k: float
    # LTC rejection variant
    is_ltc_rejected: bool
    ltc_reverted_to: int


class UpdatePerturbationEvent(SpsaEventBase, total=False):
    """``update_perturbation``: 摂動パラメータの更新（レガシー/将来用）。"""

    perturbations: dict[str, dict[str, float]]
    c_k: float
    a_k: float


class LtcRegressionStartEvent(SpsaEventBase, total=False):
    """``ltc_regression_start``: LTC 回帰テストの開始。"""

    total_pairs: int
    tuned_variant_token: str
    baseline_update_idx: int
    baseline_variant_token: str


class LtcRegressionResultEvent(SpsaEventBase, total=False):
    """``ltc_regression_result``: LTC 回帰テストの結果。"""

    status: str
    winrate: float | None
    elo: float | None
    tuned_wins: int
    baseline_wins: int
    draws: int
    total_games: int
    pairs_played: int
    fail_reasons: list[str]
    is_accepted: bool
    tuned_variant_token: str
    baseline_update_idx: int
    baseline_variant_token: str
    sprt: JsonObject | None
    sprt_decision: str | None


# ---------------------------------------------------------------------------
# Union
# ---------------------------------------------------------------------------

SpsaEvent = (
    SpsaEventBase
    | GameScheduledEvent
    | GameResultEvent
    | UpdatePendingEvent
    | UpdateEvent
    | UpdatePerturbationEvent
    | LtcRegressionStartEvent
    | LtcRegressionResultEvent
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_spsa_event(raw: Mapping[str, JsonValue]) -> SpsaEvent:
    """生の dict を型付き SPSA イベントに変換する。

    読み込み境界で1回だけ呼び出し、以降は型安全にアクセスできるようにする。
    """
    raw_map = to_json_object(raw)
    try:
        normalized_dump = _RawSpsaEventModel.model_validate(raw_map).model_dump(mode="python", by_alias=True)
        normalized_raw = to_json_object(normalized_dump)
    except ValidationError:
        normalized_raw = raw_map

    event_type = coerce_str(normalized_raw.get("event")) or ""

    # 共通フィールド
    base: JsonObject = {"event": event_type}
    if (v := coerce_str(normalized_raw.get("session_uuid"))) is not None:
        base["session_uuid"] = v
    if (v := coerce_timestamp_ms(normalized_raw.get("ts"))) is not None:
        base["ts"] = v
    if (v := coerce_int(normalized_raw.get("update_idx"))) is not None:
        base["update_idx"] = v
    if (v := coerce_str(normalized_raw.get("family"))) is not None:
        base["family"] = v
    raw_is_ltc = normalized_raw.get("is_ltc")
    if isinstance(raw_is_ltc, bool):
        base["is_ltc"] = raw_is_ltc

    if event_type == "game_scheduled":
        return GameScheduledEvent(**parse_game_scheduled_payload(normalized_raw, base))
    if event_type == "game_result":
        return GameResultEvent(**parse_game_result_payload(normalized_raw, base))
    if event_type == "update_pending":
        return UpdatePendingEvent(**parse_update_pending_payload(normalized_raw, base))
    if event_type == "update":
        return UpdateEvent(**parse_update_payload(normalized_raw, base))
    if event_type == "update_perturbation":
        return UpdatePerturbationEvent(**parse_update_perturbation_payload(normalized_raw, base))
    if event_type == "ltc_regression_start":
        return LtcRegressionStartEvent(**parse_ltc_regression_start_payload(normalized_raw, base))
    if event_type == "ltc_regression_result":
        return LtcRegressionResultEvent(**parse_ltc_regression_result_payload(normalized_raw, base))

    # 未知のイベント型はベースフィールドのみ返す
    return SpsaEventBase(**base)
