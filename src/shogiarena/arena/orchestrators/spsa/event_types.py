"""SPSA イベントの型定義。

events.jsonl に永続化されるイベントの TypedDict 定義とパーサー。
全イベントは ``total=False`` で定義し、旧バージョンのデータとの互換性を維持する。
"""

from __future__ import annotations

from typing import Any, TypedDict

from shogiarena.utils.types.coerce import (
    coerce_float,
    coerce_int,
    coerce_str,
    coerce_timestamp_ms,
)

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
    ltc: bool


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
    tuned_as_black: bool
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
    tuned_as_black: bool
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
    result_code: int | None


class UpdatePendingEvent(SpsaEventBase, total=False):
    """``update_pending``: パラメータ更新の準備開始。"""

    params: dict[str, float]
    timestamp: int
    perturbations: dict[str, dict[str, float]]
    pending: bool
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
    ltc_rejected: bool
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
    accepted: bool
    tuned_variant_token: str
    baseline_update_idx: int
    baseline_variant_token: str
    sprt: dict[str, object] | None
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


def _coerce_str_dict(raw: object) -> dict[str, float]:
    """dict[str, float] に変換。入力が dict でなければ空 dict を返す。"""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for k, v in raw.items():
        fv = coerce_float(v)
        if fv is not None:
            result[str(k)] = fv
    return result


def _coerce_perturbations(raw: object) -> dict[str, dict[str, float]]:
    """摂動辞書を正規化する。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k): _coerce_str_dict(v) for k, v in raw.items()}


def parse_spsa_event(raw: dict[str, Any]) -> SpsaEvent:
    """生の dict を型付き SPSA イベントに変換する。

    読み込み境界で1回だけ呼び出し、以降は型安全にアクセスできるようにする。
    """
    event_type = str(raw.get("event", ""))

    # 共通フィールド
    base: dict[str, Any] = {"event": event_type}
    if (v := coerce_str(raw.get("session_uuid"))) is not None:
        base["session_uuid"] = v
    if (v := coerce_timestamp_ms(raw.get("ts"))) is not None:
        base["ts"] = v
    if (v := coerce_int(raw.get("update_idx"))) is not None:
        base["update_idx"] = v
    if (v := coerce_str(raw.get("family"))) is not None:
        base["family"] = v
    raw_ltc = raw.get("ltc")
    if isinstance(raw_ltc, bool):
        base["ltc"] = raw_ltc

    if event_type == "game_scheduled":
        return _parse_game_scheduled(raw, base)
    if event_type == "game_result":
        return _parse_game_result(raw, base)
    if event_type == "update_pending":
        return _parse_update_pending(raw, base)
    if event_type == "update":
        return _parse_update(raw, base)
    if event_type == "update_perturbation":
        return _parse_update_perturbation(raw, base)
    if event_type == "ltc_regression_start":
        return _parse_ltc_regression_start(raw, base)
    if event_type == "ltc_regression_result":
        return _parse_ltc_regression_result(raw, base)

    # 未知のイベント型はベースフィールドのみ返す
    return SpsaEventBase(**base)


def _set_optional_str(result: dict[str, Any], raw: dict[str, Any], key: str) -> None:
    if (v := coerce_str(raw.get(key))) is not None:
        result[key] = v


def _set_optional_int(result: dict[str, Any], raw: dict[str, Any], key: str) -> None:
    if (v := coerce_int(raw.get(key))) is not None:
        result[key] = v


def _set_optional_float(result: dict[str, Any], raw: dict[str, Any], key: str) -> None:
    if (v := coerce_float(raw.get(key))) is not None:
        result[key] = v


def _set_optional_bool(result: dict[str, Any], raw: dict[str, Any], key: str) -> None:
    raw_val = raw.get(key)
    if isinstance(raw_val, bool):
        result[key] = raw_val


def _parse_game_scheduled(raw: dict[str, Any], base: dict[str, Any]) -> GameScheduledEvent:
    result = dict(base)
    for key in (
        "game_id",
        "variant_token",
        "variant_label",
        "tuned_variant_token",
        "baseline_variant_token",
        "phase",
        "black_player",
        "white_player",
        "status",
        "assigned_instance",
        "start_time",
    ):
        _set_optional_str(result, raw, key)
    _set_optional_bool(result, raw, "tuned_as_black")
    _set_optional_int(result, raw, "worker_idx")
    # assigned_instance / start_time は None を許容
    if "assigned_instance" not in result and "assigned_instance" in raw:
        result["assigned_instance"] = None
    if "start_time" not in result and "start_time" in raw:
        result["start_time"] = None
    return GameScheduledEvent(**result)


def _parse_game_result(raw: dict[str, Any], base: dict[str, Any]) -> GameResultEvent:
    result = dict(base)
    _set_optional_int(result, raw, "winner")
    _set_optional_bool(result, raw, "tuned_as_black")
    for key in (
        "game_id",
        "phase",
        "tuned_variant",
        "baseline_variant",
        "variant_token",
        "variant_label",
        "black_player",
        "white_player",
        "initial_sfen",
        "time_control_black",
        "time_control_white",
        "end_time",
    ):
        _set_optional_str(result, raw, key)
    _set_optional_int(result, raw, "num_moves")
    _set_optional_int(result, raw, "result_code")
    return GameResultEvent(**result)


def _parse_update_pending(raw: dict[str, Any], base: dict[str, Any]) -> UpdatePendingEvent:
    result = dict(base)
    raw_params = raw.get("params")
    if isinstance(raw_params, dict):
        result["params"] = _coerce_str_dict(raw_params)
    if (v := coerce_timestamp_ms(raw.get("timestamp"))) is not None:
        result["timestamp"] = v
    raw_perturbations = raw.get("perturbations")
    if isinstance(raw_perturbations, dict):
        result["perturbations"] = _coerce_perturbations(raw_perturbations)
    _set_optional_bool(result, raw, "pending")
    _set_optional_float(result, raw, "c_k")
    _set_optional_float(result, raw, "a_k")
    return UpdatePendingEvent(**result)


def _parse_update(raw: dict[str, Any], base: dict[str, Any]) -> UpdateEvent:
    result = dict(base)
    raw_params = raw.get("params")
    if isinstance(raw_params, dict):
        result["params"] = _coerce_str_dict(raw_params)
    if (v := coerce_timestamp_ms(raw.get("timestamp"))) is not None:
        result["timestamp"] = v
    for fkey in ("s_plus", "s_minus", "step", "delta_norm", "c_k", "a_k"):
        _set_optional_float(result, raw, fkey)
    raw_gradients = raw.get("gradients")
    if isinstance(raw_gradients, dict):
        result["gradients"] = _coerce_str_dict(raw_gradients)
    raw_deltas = raw.get("deltas")
    if isinstance(raw_deltas, dict):
        result["deltas"] = _coerce_str_dict(raw_deltas)
    for ikey in ("batch_size", "total_games", "ltc_reverted_to"):
        _set_optional_int(result, raw, ikey)
    raw_perturbations = raw.get("perturbations")
    if isinstance(raw_perturbations, dict):
        result["perturbations"] = _coerce_perturbations(raw_perturbations)
    _set_optional_bool(result, raw, "ltc_rejected")
    return UpdateEvent(**result)


def _parse_update_perturbation(raw: dict[str, Any], base: dict[str, Any]) -> UpdatePerturbationEvent:
    result = dict(base)
    raw_perturbations = raw.get("perturbations")
    if isinstance(raw_perturbations, dict):
        result["perturbations"] = _coerce_perturbations(raw_perturbations)
    _set_optional_float(result, raw, "c_k")
    _set_optional_float(result, raw, "a_k")
    return UpdatePerturbationEvent(**result)


def _parse_ltc_regression_start(raw: dict[str, Any], base: dict[str, Any]) -> LtcRegressionStartEvent:
    result = dict(base)
    _set_optional_int(result, raw, "total_pairs")
    _set_optional_str(result, raw, "tuned_variant_token")
    _set_optional_int(result, raw, "baseline_update_idx")
    _set_optional_str(result, raw, "baseline_variant_token")
    return LtcRegressionStartEvent(**result)


def _parse_ltc_regression_result(raw: dict[str, Any], base: dict[str, Any]) -> LtcRegressionResultEvent:
    result = dict(base)
    _set_optional_str(result, raw, "status")
    # winrate / elo は None を許容
    winrate = raw.get("winrate")
    if winrate is not None:
        result["winrate"] = coerce_float(winrate)
    elo = raw.get("elo")
    if elo is not None:
        result["elo"] = coerce_float(elo)
    for ikey in ("tuned_wins", "baseline_wins", "draws", "total_games", "pairs_played", "baseline_update_idx"):
        _set_optional_int(result, raw, ikey)
    _set_optional_bool(result, raw, "accepted")
    _set_optional_str(result, raw, "tuned_variant_token")
    _set_optional_str(result, raw, "baseline_variant_token")
    _set_optional_str(result, raw, "sprt_decision")
    # fail_reasons
    raw_fail = raw.get("fail_reasons")
    if isinstance(raw_fail, list):
        result["fail_reasons"] = [str(x) for x in raw_fail]
    # sprt (pass through if dict)
    raw_sprt = raw.get("sprt")
    if isinstance(raw_sprt, dict):
        result["sprt"] = raw_sprt
    elif raw_sprt is None:
        result["sprt"] = None
    return LtcRegressionResultEvent(**result)
