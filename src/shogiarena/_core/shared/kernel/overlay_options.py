from __future__ import annotations

from collections.abc import Mapping

# Top-level overlay keys that are structural rather than USI options. A legacy
# flat-form overlay file would list USI option names directly at the top level;
# any top-level key outside this set, when no `options:` block is present,
# signals that flat form, which is no longer supported.
_OVERLAY_STRUCTURAL_KEYS = frozenset({"options", "engine"})


def select_overlay_options(raw: Mapping[str, object], *, source: str) -> dict[str, object]:
    """オーバーレイ設定の `options:` ブロックを取り出す。

    オーバーレイファイルは USI オプションを必ず `options:` キー配下に宣言する。
    トップレベルに直接オプションを並べる旧フラット形式は廃止されており、
    `options`/`engine` 以外のトップレベルキーが `options:` 不在で現れた場合は
    フラット形式の誤用とみなして例外を送出する（サイレントに無視しない）。

    Args:
        raw: オーバーレイ YAML をパースしたマッピング。
        source: エラーメッセージ用の発生源（ファイルパスやフィールド名）。

    Returns:
        `options:` 配下のマッピング（キーは `str` 化済み）。未宣言かつ構造キーのみの
        場合は空辞書。

    Raises:
        TypeError: フラット形式が検出された場合、または `options` の値が
            マッピングでない場合。
    """
    if "options" in raw:
        options = raw["options"]
        if options is None:
            return {}
        if not isinstance(options, Mapping):
            raise TypeError(f"overlay options must be a mapping: {source}")
        return {str(key): value for key, value in options.items()}
    stray = sorted(str(key) for key in raw if key not in _OVERLAY_STRUCTURAL_KEYS)
    if stray:
        raise TypeError(
            f"overlay must declare USI options under an 'options:' block: {source} "
            f"(found top-level keys {stray}; the flat top-level form is no longer supported)"
        )
    return {}


__all__ = ["select_overlay_options"]
