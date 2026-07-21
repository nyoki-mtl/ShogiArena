"""内蔵定跡(A) の provenance（使用定跡の記録）ビルダー。Task 0015。

engine metadata と per-participation metadata に載せる ``book`` サマリを構築する。
専用 DB テーブルは作らず、既存 metadata 経路に乗せる（Decision 6）。

fingerprint は 2.4GB 級でも起動前コストを抑えるため、既定は軽量方式
(file size + mtime + 先頭/末尾の部分 hash + basename)。full-file hash は opt-in。

信頼源: ``agent-docs/architecture/opening-book-and-openings.md`` §5。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.shared.kernel.engine_book import (
    is_engine_book_enabled,
    resolve_engine_book_path,
    resolve_yaneuraou_book_fallback_path,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)

# Book タブの集計と再現で意味のある主要 book option。
KEY_BOOK_OPTIONS: tuple[str, ...] = (
    "USI_OwnBook",
    "BookDir",
    "BookFile",
    "BookOnTheFly",
    "BookMoves",
    "NarrowBook",
    "BookEvalDiff",
    "BookDepthLimit",
    "ConsiderBookMoveCount",
)

# 軽量 fingerprint で読む先頭/末尾バイト数。
_PARTIAL_HASH_BYTES = 65536


def _partial_sha256(path: Path, size: int) -> str:
    """先頭/末尾の bounded バイトとサイズから部分 hash を計算する（全読みしない）。"""

    hasher = hashlib.sha256()
    hasher.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        head = handle.read(_PARTIAL_HASH_BYTES)
        hasher.update(head)
        if size > _PARTIAL_HASH_BYTES:
            tail_start = max(size - _PARTIAL_HASH_BYTES, _PARTIAL_HASH_BYTES)
            handle.seek(tail_start)
            hasher.update(handle.read(_PARTIAL_HASH_BYTES))
    return hasher.hexdigest()


def _full_sha256(path: Path) -> str:
    """全読み hash（高コスト・opt-in 専用）。"""

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fingerprint_file(path: str | Path, *, full_hash: bool = False) -> JsonObject:
    """path resource の軽量 fingerprint（既定）または full-file hash（opt-in）を返す。

    Returns:
        ``path`` / ``basename`` / ``size`` / ``mtime`` / ``hash_method`` と、軽量時は
        ``partial_sha256``、opt-in 時は ``full_sha256`` を含む JSON object。
        stat できない場合は ``hash_method == "missing"``。
    """

    candidate = Path(path)
    fingerprint: JsonObject = {"path": str(candidate), "basename": candidate.name}
    try:
        stat = candidate.stat()
    except OSError:
        fingerprint["hash_method"] = "missing"
        return fingerprint

    size = stat.st_size
    fingerprint["size"] = size
    fingerprint["mtime"] = stat.st_mtime
    try:
        if full_hash:
            fingerprint["hash_method"] = "full_sha256"
            fingerprint["full_sha256"] = _full_sha256(candidate)
        else:
            fingerprint["hash_method"] = "size_partial_sha256"
            fingerprint["partial_sha256"] = _partial_sha256(candidate, size)
    except OSError as exc:
        logger.debug("Failed to hash book file %s: %s", candidate, exc)
        fingerprint["hash_method"] = "size_only"
    return fingerprint


def build_book_provenance(
    options: Mapping[str, JsonValue],
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    full_hash: bool = False,
) -> JsonObject | None:
    """内蔵定跡が有効なら ``book`` provenance サマリを返す。無効なら ``None``。

    軽量 fingerprint を既定とし、絶対パスかつ存在する book のときのみ fingerprint を付ける。
    """

    if not is_engine_book_enabled(options):
        return None

    provenance: JsonObject = {}
    resolved = resolve_engine_book_path(options, output_dir=output_dir, engine_dir=engine_dir)
    if resolved is not None:
        resolved_path = Path(resolved)
        actual_path = (
            resolve_yaneuraou_book_fallback_path(resolved_path) if resolved_path.is_absolute() else resolved_path
        )
        provenance["resolved_path"] = str(actual_path)
        if actual_path != resolved_path:
            provenance["requested_path"] = resolved
            provenance["fallback"] = "ybb"
        if actual_path.is_absolute():
            provenance["fingerprint"] = fingerprint_file(actual_path, full_hash=full_hash)

    provenance["options"] = {key: json_serialize(options[key]) for key in KEY_BOOK_OPTIONS if key in options}
    return provenance


__all__ = [
    "KEY_BOOK_OPTIONS",
    "build_book_provenance",
    "fingerprint_file",
]
