"""USI transcript artifact naming contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

TranscriptRole = Literal["black", "white"]

_SAFE_FILE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_transcript_game_id(game_id: str) -> str:
    """Transcript ファイル名に使える game id へ正規化する。"""

    normalized = _SAFE_FILE_RE.sub("-", str(game_id).strip()).strip("-")
    return normalized or "unknown"


def transcript_path_for_game(run_dir: Path, *, game_id: str, role: TranscriptRole) -> Path:
    """指定 game/role の transcript path を返す。"""

    safe_game_id = safe_transcript_game_id(game_id)
    return run_dir / "transcripts" / f"game-{safe_game_id}-{role}.log"


__all__ = ["TranscriptRole", "safe_transcript_game_id", "transcript_path_for_game"]
