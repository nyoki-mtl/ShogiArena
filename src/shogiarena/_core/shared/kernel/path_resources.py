"""エンジンの path resource（scalar / composite）モデルと解決ロジック。

**path resource** とは、エンジンが実行時に必要とするファイル/ディレクトリのこと。
ShogiArena は path resource の実体パスを preflight 存在確認・provenance 記録・
remote 配布に使う一方、エンジンへ送る USI option は engine 互換の表現を維持する。

- **scalar resource**: 単一 option 値が実体パスを指す
  (``EvalDir`` / ``BookDir`` / ``DNN_Model`` と、ユーザー宣言の ``path_options``)。
- **composite resource**: directory option と file option の組で実体パスが決まる
  (``EvalDir`` + ``EvalFile`` / ``BookDir`` + ``BookFile``)。
  YaneuraOu の ``Path::Combine`` 挙動に合わせて解決する。

設計の信頼源は ``agent-docs/architecture/opening-book-and-openings.md`` §3。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shogiarena._core.shared.kernel.paths import PATH_OPTION_KEYS, resolve_path_like

# ``BookFile`` に対する「定跡無効」センチネル。実体パスを持たないため、
# preflight / provenance を行う consumer 側で除外に使う（解決自体は機械的に行う）。
BOOK_DISABLED_VALUES: frozenset[str] = frozenset({"no_book"})


@dataclass(frozen=True, slots=True)
class CompositeResourceSpec:
    """composite path resource の (dir, file) 組と engine 既定 directory。"""

    file_key: str
    dir_key: str
    default_dir: str


# YaneuraOu 互換の composite resource 定義。
# ``default_dir`` は dir option 省略時に engine が使う既定 directory
# (``BookDir`` 既定 ``"book"`` = book.cpp:1039 / ``EvalDir`` 既定 ``"eval"``)。
COMPOSITE_RESOURCE_SPECS: tuple[CompositeResourceSpec, ...] = (
    CompositeResourceSpec(file_key="EvalFile", dir_key="EvalDir", default_dir="eval"),
    CompositeResourceSpec(file_key="BookFile", dir_key="BookDir", default_dir="book"),
)


@dataclass(frozen=True, slots=True)
class PathResource:
    """解決済みの path resource 1 件。

    Attributes:
        kind: ``"scalar"`` または ``"composite"``。
        option_keys: この resource を構成する USI option キー
            (scalar は 1 件、composite は存在する dir/file キー)。
        original_values: engine へ渡す元の option 値（キー -> 値）。
        resolved_path: 解決後の実体ファイルシステムパス（存在確認は行わない）。
    """

    kind: Literal["scalar", "composite"]
    option_keys: tuple[str, ...]
    original_values: dict[str, str]
    resolved_path: str


def is_absolute_path_like(value: str) -> bool:
    """YaneuraOu ``Path::IsAbsolute`` 相当の絶対パス判定。

    先頭が ``/`` ``\\`` ``~``、または 2 文字目が ``:``（ドライブレター）なら絶対とみなす。
    """

    if not value:
        return False
    head = value[0]
    if head in ("/", "\\", "~"):
        return True
    return len(value) >= 2 and value[1] == ":"


def combine_path(folder: str, filename: str) -> str:
    """YaneuraOu ``Path::Combine`` 相当の結合。

    ``filename`` が絶対なら ``filename`` をそのまま返す。相対なら ``folder`` と
    区切り文字を補って結合する（misc.cpp:1417 と同仕様）。
    """

    if is_absolute_path_like(filename):
        return filename
    if folder and folder[-1] not in ("/", "\\"):
        return f"{folder}/{filename}"
    return f"{folder}{filename}"


def resolve_composite_path(
    spec: CompositeResourceSpec,
    *,
    file_value: str,
    dir_value: str | None,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> str:
    """composite resource の実体パスを YaneuraOu 互換 Combine で解決する。

    Args:
        spec: 対象 composite spec。
        file_value: file option の生値（例: ``"user_book1.db"`` / 絶対パス）。
        dir_value: dir option の生値。``None`` または空なら ``spec.default_dir`` を使う。
        output_dir: ``{output_dir}`` プレースホルダの基準。
        engine_dir: ``{engine_dir}`` プレースホルダの基準。

    Returns:
        解決後の実体パス文字列。相対結果（engine cwd 基準）になる場合もある。
    """

    resolved_file = resolve_path_like(file_value, output_dir=output_dir, engine_dir=engine_dir)
    if is_absolute_path_like(resolved_file):
        return resolved_file
    if dir_value is not None and dir_value.strip():
        resolved_dir = resolve_path_like(dir_value, output_dir=output_dir, engine_dir=engine_dir)
    else:
        resolved_dir = spec.default_dir
    return combine_path(resolved_dir, resolved_file)


def _string_options(options: Mapping[str, object]) -> dict[str, str]:
    """str 値の option だけを取り出す（空白のみは除外）。"""

    result: dict[str, str] = {}
    for key, value in options.items():
        if isinstance(value, str) and value.strip():
            result[str(key)] = value
    return result


def resolve_path_resources(
    options: Mapping[str, object],
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    extra_scalar_keys: Iterable[str] = (),
) -> list[PathResource]:
    """option mapping から scalar / composite path resource を解決する。

    composite を先に解決し、組に取り込まれた dir option は scalar として重複出力しない。
    本関数は実体パスを返すのみで、存在確認・センチネル（``no_book``）除外は行わない。
    それらは consumer 側（book 検証 / provenance）が担う。

    Args:
        options: エンジンへ渡す（マージ済み）USI option mapping。
        output_dir: ``{output_dir}`` プレースホルダの基準。
        engine_dir: ``{engine_dir}`` プレースホルダの基準。
        extra_scalar_keys: ユーザー宣言の追加 scalar path option キー。

    Returns:
        解決済み :class:`PathResource` のリスト（option キー昇順で安定）。
    """

    string_opts = _string_options(options)
    consumed: set[str] = set()
    resources: list[PathResource] = []

    # 1) composite を先に解決し、dir/file キーを consumed に記録する。
    for spec in COMPOSITE_RESOURCE_SPECS:
        file_value = string_opts.get(spec.file_key)
        if file_value is None:
            continue
        dir_value = string_opts.get(spec.dir_key)
        resolved = resolve_composite_path(
            spec,
            file_value=file_value,
            dir_value=dir_value,
            output_dir=output_dir,
            engine_dir=engine_dir,
        )
        present_keys: list[str] = [spec.dir_key] if dir_value is not None else []
        present_keys.append(spec.file_key)
        original = {key: string_opts[key] for key in present_keys}
        resources.append(
            PathResource(
                kind="composite",
                option_keys=tuple(present_keys),
                original_values=original,
                resolved_path=resolved,
            )
        )
        consumed.update(present_keys)

    # 2) scalar resource を解決する（composite に取り込まれた dir は除外）。
    scalar_keys = set(PATH_OPTION_KEYS) | {str(key) for key in extra_scalar_keys}
    for key in sorted(scalar_keys):
        if key in consumed:
            continue
        value = string_opts.get(key)
        if value is None:
            continue
        resolved = resolve_path_like(value, output_dir=output_dir, engine_dir=engine_dir)
        resources.append(
            PathResource(
                kind="scalar",
                option_keys=(key,),
                original_values={key: value},
                resolved_path=resolved,
            )
        )

    return resources


__all__ = [
    "BOOK_DISABLED_VALUES",
    "COMPOSITE_RESOURCE_SPECS",
    "CompositeResourceSpec",
    "PathResource",
    "combine_path",
    "is_absolute_path_like",
    "resolve_composite_path",
    "resolve_path_resources",
]
