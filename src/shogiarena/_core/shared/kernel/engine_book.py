"""内蔵定跡(A) の起動前バリデーションと有効判定。Task 0014。

``USI_OwnBook`` が明示的に ``false`` でなく、``BookFile`` が ``no_book`` でないとき、
``BookDir + BookFile`` を YaneuraOu 互換 Combine で実体パスに解決し、存在確認と
rshogi ``YaneuraOuBook`` による bounded 健全性チェックを行う。検証失敗はエンジン起動前に
明確なエラーとして surface する（黙って no_book 扱いで完走するのを防ぐ）。

信頼源: ``agent-docs/architecture/opening-book-and-openings.md`` §4。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.path_resources import (
    BOOK_DISABLED_VALUES,
    COMPOSITE_RESOURCE_SPECS,
    resolve_composite_path,
)

logger = logging.getLogger(__name__)

_BOOK_SPEC = next(spec for spec in COMPOSITE_RESOURCE_SPECS if spec.file_key == "BookFile")

# ``USI_OwnBook`` を明示的に無効化したとみなす値（大文字小文字無視）。
_FALSE_LITERALS: frozenset[str] = frozenset({"false", "0", "off", "no"})


def _is_explicitly_false(value: JsonValue) -> bool:
    """USI option 値が「明示的に false」かを判定する。"""

    if isinstance(value, bool):
        return value is False
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in _FALSE_LITERALS
    return False


def _string_option(options: Mapping[str, JsonValue], key: str) -> str | None:
    value = options.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def is_engine_book_enabled(options: Mapping[str, JsonValue]) -> bool:
    """内蔵定跡が有効になりうる構成かを判定する。

    発動条件は「``USI_OwnBook`` が ``false`` で明示無効化されておらず、かつ
    ``BookFile`` が指定されていて ``no_book`` でない」。YaneuraOu は ``USI_OwnBook``
    既定が ``true`` のため、省略時も有効とみなす（book.cpp:1003）。
    """

    book_file = _string_option(options, "BookFile")
    if book_file is None or book_file.strip() in BOOK_DISABLED_VALUES:
        return False
    if "USI_OwnBook" in options and _is_explicitly_false(options["USI_OwnBook"]):
        return False
    return True


def resolve_engine_book_path(
    options: Mapping[str, JsonValue],
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> str | None:
    """内蔵定跡が有効なら ``BookDir + BookFile`` の実体パスを返す。無効なら ``None``。"""

    if not is_engine_book_enabled(options):
        return None
    book_file = _string_option(options, "BookFile")
    if book_file is None:
        return None
    return resolve_composite_path(
        _BOOK_SPEC,
        file_value=book_file,
        dir_value=_string_option(options, "BookDir"),
        output_dir=output_dir,
        engine_dir=engine_dir,
    )


def collect_book_preflight_errors(
    options: Mapping[str, JsonValue],
    *,
    engine_name: str | None,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    working_dir: Path | None = None,
) -> list[str]:
    """内蔵定跡の起動前バリデーションを行い、エラーメッセージ一覧を返す。

    内蔵定跡が無効なら空リスト。``BookDir + BookFile`` の解決後パスについて存在確認と
    bounded 健全性検証を行う。相対パス（``BookDir: book`` のような自然な指定）は、エンジンの
    作業ディレクトリ基準で評価されるべきだが、それが不明な場合は ``working_dir``（無指定なら
    現在の作業ディレクトリ）を基準に解決して存在確認する。これにより typo / missing file を
    取りこぼさず、「黙って no_book 完走」を防ぐ。
    """

    resolved = resolve_engine_book_path(options, output_dir=output_dir, engine_dir=engine_dir)
    if resolved is None:
        return []

    candidate = Path(resolved)
    if not candidate.is_absolute():
        base = working_dir if working_dir is not None else Path.cwd()
        candidate = base / candidate

    if not candidate.exists():
        return [
            f"Engine '{engine_name}' opening book file does not exist: {candidate} "
            "(set BookFile=no_book or USI_OwnBook=false to disable the built-in book)"
        ]

    health_error = _validate_book_health(engine_name=engine_name, path=candidate)
    return [health_error] if health_error is not None else []


def _validate_book_health(*, engine_name: str | None, path: Path) -> str | None:
    """rshogi で book を bounded 検証し、問題があればエラーメッセージを返す。

    全読みは行わない（``open()`` は先頭の bounded prefix のみ検証する）。
    2.4GB 級の DB でも起動前コストを抑えられる。
    """

    try:
        from rshogi.book import YaneuraOuBook
    except ImportError:
        logger.debug("rshogi.book unavailable; skipping book health check for engine '%s'", engine_name)
        return None

    try:
        book = YaneuraOuBook.open(str(path))
        diagnostics = book.diagnostics()
    except ValueError as exc:
        return f"Engine '{engine_name}' opening book is not a readable YaneuraOu DB: {path}: {exc}"

    kind = diagnostics.kind
    if kind == "invalid_header":
        return f"Engine '{engine_name}' opening book has an invalid YaneuraOu DB header: {path}"
    if kind == "unsorted":
        return (
            f"Engine '{engine_name}' opening book is not sorted (line {diagnostics.line_number}): {path}; "
            "YaneuraOu on-the-fly lookup requires an SFEN-sorted DB"
        )
    if kind == "sorted" and not diagnostics.checked_rows:
        return f"Engine '{engine_name}' opening book contains no book entries: {path} (wrong file or empty book?)"
    if kind == "sorted" and diagnostics.complete is False:
        logger.info(
            "Engine '%s' opening book validated a bounded prefix (%s rows); full sort not verified: %s",
            engine_name,
            diagnostics.checked_rows,
            path,
        )
    return None


__all__ = [
    "collect_book_preflight_errors",
    "is_engine_book_enabled",
    "resolve_engine_book_path",
]
