from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from shogiarena.arena.storage import RunStorage


@dataclass(slots=True, kw_only=True)
class RunResultBase:
    """Runストレージと紐づく長期保持可能な結果メタ情報。"""

    run_id: str
    run_dir: Path
    storage: RunStorage
    summary: Mapping[str, Any] | None = None
    config_snapshot: Mapping[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def duration_ms(self) -> int | None:
        if self.started_at is None or self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return int(delta.total_seconds() * 1000)

    def serialize_extras(self) -> dict[str, object]:
        """サブクラス固有フィールドを JSON シリアライズ可能な dict で返す。"""
        return {}
