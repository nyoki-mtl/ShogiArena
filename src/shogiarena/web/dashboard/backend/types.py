"""ダッシュボードバックエンド共有 TypedDict 定義。"""

from __future__ import annotations

from typing import Any, TypeAlias, TypedDict

from shogiarena.web.dashboard.backend.live.types import LiveViewSnapshot
from shogiarena.web.dashboard.backend.match.types import MatchPayload
from shogiarena.web.dashboard.backend.sprt.types import SprtPayload
from shogiarena.web.dashboard.backend.spsa.types import SpsaSummaryPayload

# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


class SnapshotMeta(TypedDict, total=False):
    """スナップショットのメタデータ。

    ``GamesSnapshotPayload.snapshotMeta`` や差分計算で使用する共通構造。
    """

    timestamp: str
    source: str
    mode: str
    updated_at: str


# ---------------------------------------------------------------------------
# Games snapshot
# ---------------------------------------------------------------------------


class GamesSnapshotPayload(TypedDict):
    """ゲーム一覧スナップショットのエンベロープ。

    ``store_games()`` および ``compute_games_delta()`` が構築する
    ペイロード構造。``type`` は "bulk"（初回全量）または "delta"（差分）。
    """

    type: str
    rows: list[dict[str, Any]]  # I/O boundary: 各モードで異なるカラム構造を持つ
    snapshotMeta: SnapshotMeta


# ---------------------------------------------------------------------------
# Summary snapshot discriminated union
# ---------------------------------------------------------------------------

SummarySnapshot: TypeAlias = SpsaSummaryPayload | SprtPayload | MatchPayload
"""ダッシュボードサマリペイロードの discriminated union。

``mode`` フィールド (``Literal["spsa"] | Literal["sprt"] | Literal["match"]``) で判別可能。
新規のサマリモードを追加する際はここに型を追加すること。
"""

__all__ = [
    "GamesSnapshotPayload",
    "LiveViewSnapshot",
    "MatchPayload",
    "SnapshotMeta",
    "SprtPayload",
    "SpsaSummaryPayload",
    "SummarySnapshot",
]
