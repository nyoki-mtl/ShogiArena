"""Live View ダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import TypedDict


class LiveViewProgress(TypedDict, total=False):
    """Live View 進捗情報。"""

    kind: str
    unit_label: str
    completed: int | None
    total: int | None
    cancelled: int | None
    is_final: bool
    state: str
    updated_at: str | None


class LiveViewSnapshot(TypedDict):
    """``build_live_view_snapshot`` が返す Live View エンベロープ。"""

    version: int
    mode: str
    progress: LiveViewProgress | None
