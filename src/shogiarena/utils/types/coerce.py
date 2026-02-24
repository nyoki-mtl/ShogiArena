"""正規型変換ユーティリティ。

全関数の共通規約:
- ``bool`` は数値型としては **拒否** する（``int`` のサブクラスだが、ドメインでは別物）
- ``float→int`` は finiteness チェック付き truncation
- ``str`` は strip 後に変換、空文字は ``None``
- 変換失敗時は ``None`` を返す（strict バリアントは ``ValueError``）
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, overload

from rshogi.record import GameResult

__all__ = [
    "coerce_bool",
    "coerce_float",
    "coerce_game_result",
    "coerce_int",
    "coerce_int_strict",
    "coerce_str",
    "coerce_str_list",
    "coerce_timestamp_ms",
    "datetime_to_iso",
    "is_strict_int",
    "is_strict_numeric",
    "strict_int",
    "timestamp_to_iso",
]


# ---------------------------------------------------------------------------
# strict type guards – bool を排除した int / numeric 判定
# ---------------------------------------------------------------------------


def is_strict_int(value: object) -> bool:
    """``bool`` を除外した ``int`` 判定。

    Python では ``bool`` は ``int`` のサブクラスであるため、
    ``isinstance(v, int)`` は ``True/False`` にもマッチしてしまう。
    この関数は ``int`` だが ``bool`` ではない場合のみ ``True`` を返す。
    """
    match value:
        case bool():
            return False
        case int():
            return True
        case _:
            return False


def is_strict_numeric(value: object) -> bool:
    """``bool`` を除外した ``int | float`` 判定。"""
    match value:
        case bool():
            return False
        case int() | float():
            return True
        case _:
            return False


def strict_int(value: object) -> int | None:
    """``bool`` を除外して ``int`` を取り出す。非 ``int`` なら ``None``。"""
    match value:
        case bool():
            return None
        case int() as n:
            return n
        case _:
            return None


# ---------------------------------------------------------------------------
# int
# ---------------------------------------------------------------------------


def coerce_int(value: object) -> int | None:
    """``object`` を ``int`` に変換する。変換できなければ ``None`` を返す。

    * ``bool`` → ``None`` (拒否)
    * ``int``  → そのまま
    * ``float`` → finiteness チェック後に truncation (``int(value)``)
    * ``str``  → strip 後 ``int()``
    """
    match value:
        case bool():
            return None
        case int() as n:
            return n
        case float() as f:
            return int(f) if math.isfinite(f) else None
        case str() as s:
            stripped = s.strip()
            if not stripped:
                return None
            try:
                return int(stripped)
            except ValueError:
                return None
        case _:
            return None


def coerce_int_strict(value: object, field: str = "value") -> int:
    """``object`` を ``int`` に変換する。変換できなければ ``ValueError`` を送出する。"""
    result = coerce_int(value)
    if result is None:
        msg = f"{field}: int に変換できません ({value!r})"
        raise ValueError(msg)
    return result


# ---------------------------------------------------------------------------
# float
# ---------------------------------------------------------------------------


def coerce_float(value: object) -> float | None:
    """``object`` を ``float`` に変換する。変換できなければ ``None`` を返す。

    * ``bool`` → ``None`` (拒否)
    * ``int | float`` → finiteness チェック後に ``float()``
    * ``str``  → strip 後 ``float()``
    """
    match value:
        case bool():
            return None
        case int() | float() as n:
            f = float(n)
            return f if math.isfinite(f) else None
        case str() as s:
            stripped = s.strip()
            if not stripped:
                return None
            try:
                f = float(stripped)
            except ValueError:
                return None
            return f if math.isfinite(f) else None
        case _:
            return None


# ---------------------------------------------------------------------------
# bool
# ---------------------------------------------------------------------------

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSY = frozenset({"0", "false", "f", "no", "n", "off"})


def coerce_bool(value: object) -> bool:
    """``object`` を ``bool`` に変換する。

    * ``bool`` → そのまま
    * ``int | float`` → ``bool(value)``
    * ``str``  → ``{"1","true","t","yes","y","on"}`` → True、
      ``{"0","false","f","no","n","off"}`` → False
    * その他 / 認識できない文字列 → ``False``
    """
    match value:
        case bool() as b:
            return b
        case int() | float():
            return bool(value)
        case str() as s:
            if s.strip().lower() in _TRUTHY:
                return True
    return False


# ---------------------------------------------------------------------------
# str
# ---------------------------------------------------------------------------


def coerce_str(value: object) -> str | None:
    """``object`` を ``str`` に変換する。strip 後に空なら ``None``。"""
    match value:
        case str() as s:
            return s.strip() or None
        case _:
            return None


def coerce_str_list(value: object, *, field: str = "value") -> list[str]:
    """``str | list[str] | tuple[str, ...] | None`` → ``list[str]`` に正規化する。

    * ``None``         → ``[]``
    * ``str``          → ``[str]`` (strip 後に空なら ``[]``)
    * ``list | tuple`` → 各要素を ``str`` として検証 (strip 後に空ならスキップ)
    * その他           → ``TypeError``

    Parameters
    ----------
    value:
        正規化対象の値。
    field:
        エラーメッセージに含めるフィールド名。
    """
    match value:
        case None:
            return []
        case str() as s:
            stripped = s.strip()
            return [stripped] if stripped else []
        case list() | tuple() as items:
            result: list[str] = []
            for item in items:
                match item:
                    case str() as s:
                        stripped = s.strip()
                        if stripped:
                            result.append(stripped)
                    case _:
                        raise TypeError(f"{field} entries must be strings; got {type(item).__name__}")
            return result
        case _:
            raise TypeError(f"{field} must be a string or list of strings; got {type(value).__name__}")


# ---------------------------------------------------------------------------
# timestamp
# ---------------------------------------------------------------------------


def coerce_timestamp_ms(value: object) -> int | None:
    """値をミリ秒タイムスタンプに変換する。

    ``int | float`` はそのまま、``str`` は数値文字列または ISO 形式に対応。
    """
    match value:
        case bool():
            return None
        case int() as n:
            return n
        case float() as f:
            return int(f) if math.isfinite(f) else None
        case str() as s:
            raw = s.strip()
            if not raw:
                return None
            try:
                f = float(raw)
                if not math.isfinite(f):
                    return None
                return int(f)
            except (ValueError, OverflowError):
                try:
                    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
                    dt = datetime.fromisoformat(normalized)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    return None
        case _:
            return None


def timestamp_to_iso(value: object) -> str | None:
    """ミリ秒タイムスタンプを ISO 形式文字列に変換する。"""
    match value:
        case bool():
            return None
        case int() | float() as n:
            try:
                return datetime.fromtimestamp(float(n) / 1000.0, tz=timezone.utc).isoformat()
            except (OSError, OverflowError, ValueError):
                return None
        case _:
            return None


def datetime_to_iso(value: object) -> str | None:
    """``datetime | str`` を ISO 形式文字列に正規化する。

    * ``datetime`` → ``.isoformat()``
    * ``str``      → ``fromisoformat`` で検証後にそのまま返す
    * その他       → ``None``
    """
    match value:
        case datetime() as dt:
            return dt.isoformat()
        case str() as s:
            stripped = s.strip()
            if not stripped:
                return None
            try:
                datetime.fromisoformat(stripped)
            except ValueError:
                return None
            return stripped
        case _:
            return None


# ---------------------------------------------------------------------------
# GameResult
# ---------------------------------------------------------------------------


@overload
def coerce_game_result(raw: object, *, strict: Literal[True]) -> GameResult: ...


@overload
def coerce_game_result(raw: object, *, strict: Literal[False] = ...) -> GameResult | None: ...


@overload
def coerce_game_result(raw: object, *, strict: bool = ...) -> GameResult | None: ...


def coerce_game_result(raw: object, *, strict: bool = False) -> GameResult | None:
    """``object`` を ``GameResult`` に変換する。

    Parameters
    ----------
    raw:
        変換元の値。``GameResult``、``int``、``str`` を受け付ける。
    strict:
        ``True`` の場合、変換失敗時に ``ValueError`` を送出する。
        ``False`` の場合は ``None`` を返す。
    """
    match raw:
        case GameResult() as gr:
            return gr
        case bool():
            if strict:
                msg = f"bool を GameResult に変換できません: {raw!r}"
                raise ValueError(msg)
            return None
        case int() as n:
            try:
                return GameResult(n)
            except (ValueError, TypeError, OverflowError):
                if strict:
                    msg = f"無効な GameResult 値: {raw!r}"
                    raise ValueError(msg) from None
                return None
        case str() as s:
            stripped = s.strip()
            if not stripped:
                if strict:
                    msg = "空文字列を GameResult に変換できません"
                    raise ValueError(msg)
                return None
            try:
                return GameResult(int(stripped))
            except (ValueError, TypeError, OverflowError):
                if strict:
                    msg = f"文字列を GameResult に変換できません: {raw!r}"
                    raise ValueError(msg) from None
                return None
        case _:
            if strict:
                msg = f"GameResult への変換がサポートされていない型です: {raw!r}"
                raise ValueError(msg)
            return None
