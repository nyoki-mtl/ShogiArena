"""アーカイブ DB を原本のバイト列を変えずに読むための snapshot resolver。

read-only なアーカイブを SQLite で開く方法は 2 つあり、どちらも単独では使えない。

``mode=ro&immutable=1``
    「このファイルは変化しない」と宣言するので locking も WAL/journal の回復も省く。
    アーカイブツリーを一切変更しない代わりに、**checkpoint されていない WAL に入った
    commit 済みデータが読まれない。エラーも警告も出ない**(task 0066)。

``mode=ro``
    WAL は正しく読むが ``-shm`` を作る/書き換えるためアーカイブツリーを変更する。
    WAL journal mode の DB では sidecar が 1 つも無くても ``-wal`` と ``-shm`` を
    新規作成する。hot な rollback journal に対しては ``SQLITE_READONLY_ROLLBACK`` で
    open 自体が失敗し、書き込み不可な媒体では常に失敗する。

そこで、回復すべき sidecar を持つ DB だけを一時領域へ family ごと複製し、**複製側で**
WAL を畳んでから読ませる。原本は SQLite で開かない(``mode=ro`` でも開かない)。
畳んだあとの複製は sidecar を持たないので、下流は ``immutable=1`` で安全に開ける。

**この resolver が「sidecar ゼロ」を保証できるのは、read-only アーカイブの全読み取り
経路が ``immutable=1`` を使う場合に限る。** 1 つでも ``mode=ro`` で原本を開く経路が
残っていると、そこが原本に sidecar を作り、次回の起動で自分の作った sidecar を検出して
複製を発動する自己汚染ループになる。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Final

from shogiarena._core.contexts.dashboard.ports.archive_snapshot_ports import (
    ArchiveDatabaseLivenessError,
    ArchiveDatabaseSnapshotError,
)

logger = logging.getLogger(__name__)

SIDECAR_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm", "-journal")
"""SQLite が DB 本体の隣に置く補助ファイルの接尾辞。"""

_COPIED_SUFFIXES: Final[tuple[str, ...]] = ("", "-wal", "-journal")
"""複製する family。``-shm`` は共有メモリの写しで、複製側の recovery が作り直す。"""

_COPY_ATTEMPT_LIMIT: Final = 3
_COPY_RETRY_BACKOFF_S: Final = 0.1
_DIGEST_CHUNK_BYTES: Final = 1 << 20


class ArchiveDatabaseSnapshot:
    """アーカイブの DB をどこから開くかの解決結果。

    複製が要る DB だけが一時領域に materialize され、冷えている DB は原本のまま参照する。
    棋譜や ``data/*.js`` のような DB 以外の artifact は**原本の run ディレクトリから
    読むこと**。ここには複製されていない。
    """

    def __init__(
        self,
        *,
        run_dir: Path,
        materialized: dict[Path, Path],
        temporary_dir: TemporaryDirectory[str] | None,
    ) -> None:
        self._run_dir = run_dir
        self._materialized = materialized
        self._temporary_dir = temporary_dir

    def database_dir_for(self, relative_database_path: Path) -> Path:
        """Return the directory the given database must be opened from."""

        return self._materialized.get(relative_database_path, self._run_dir)

    def database_path_for(self, relative_database_path: Path) -> Path:
        """Return the resolved path of the given database."""

        return self.database_dir_for(relative_database_path) / relative_database_path

    @property
    def materialized_database_paths(self) -> tuple[Path, ...]:
        """Return the relative paths that were copied into the temporary area."""

        return tuple(sorted(self._materialized, key=lambda path: path.as_posix()))

    def close(self) -> None:
        """Remove the temporary copies, if any。

        Windows では、まだ接続を掴んでいる相手が居ると削除に失敗する。掴んでいる相手が
        居るのは teardown が途中で落ちたときなので、**そのときの本当のエラーを
        削除失敗で覆い隠さない**。消し残しは OS の temp に任せて警告だけ出す。
        """

        if self._temporary_dir is not None:
            snapshot_dir = Path(self._temporary_dir.name)
            self._temporary_dir.cleanup()
            self._temporary_dir = None
            if snapshot_dir.exists():
                logger.warning(
                    "Left the read-only archive snapshot at %s because it is still in use; "
                    "the operating system will reclaim it",
                    snapshot_dir,
                )
        self._materialized = {}

    def __enter__(self) -> ArchiveDatabaseSnapshot:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()


def resolve_archive_databases(
    run_dir: Path,
    *,
    relative_database_paths: Sequence[Path],
) -> ArchiveDatabaseSnapshot:
    """回復すべき sidecar を持つ DB だけを一時領域へ複製して解決する。

    Args:
        run_dir: アーカイブ run ディレクトリ。
        relative_database_paths: ``run_dir`` からの DB 相対パス。
            存在しないものは無視する。

    Returns:
        DB ごとの解決結果。sidecar が無い DB は原本を指し、複製は発生しない。

    Raises:
        ArchiveDatabaseLivenessError: writer が DB を掴んでいる(run が実行中に見える)。
        ArchiveDatabaseSnapshotError: 複製側で WAL を畳めなかった。
    """

    hot = [
        relative
        for relative in relative_database_paths
        if (run_dir / relative).is_file() and _has_recoverable_sidecar(run_dir / relative)
    ]
    if not hot:
        return ArchiveDatabaseSnapshot(run_dir=run_dir, materialized={}, temporary_dir=None)

    total_bytes = sum((run_dir / relative).stat().st_size for relative in hot)
    logger.info(
        "Preparing a read-only snapshot of %s in %s (%.1f MB) because it has an un-checkpointed "
        "write-ahead log or a hot journal; the archive itself is left untouched",
        ", ".join(relative.as_posix() for relative in hot),
        run_dir,
        total_bytes / 1_000_000,
    )
    temporary_dir = TemporaryDirectory(prefix="shogiarena-archive-", ignore_cleanup_errors=True)
    try:
        snapshot_dir = Path(temporary_dir.name)
        materialized: dict[Path, Path] = {}
        for relative in hot:
            materialize_quiescent_database(run_dir / relative, snapshot_dir / relative)
            materialized[relative] = snapshot_dir
    except BaseException:
        temporary_dir.cleanup()
        raise
    return ArchiveDatabaseSnapshot(run_dir=run_dir, materialized=materialized, temporary_dir=temporary_dir)


def materialize_quiescent_database(source: Path, destination: Path) -> None:
    """静止した DB family を複製し、複製側で WAL を畳んで sidecar を消す。

    原本は SQLite で開かない。純粋なファイル複製なので書き込み不可な媒体でも通る。

    Raises:
        ArchiveDatabaseLivenessError: writer が掴んでいて静止した複製を作れない。
        ArchiveDatabaseSnapshotError: 複製側で WAL を畳めなかった。
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy_quiescent_family(source, destination)
    _fold_sidecars(destination)


def _has_recoverable_sidecar(db_path: Path) -> bool:
    """回復しなければ読み落とす内容が sidecar にあるかを返す。

    長さ 0 の ``-wal`` には回復すべきフレームが無い。``-shm`` は共有メモリの写しで
    それ自体に commit を持たず、``immutable=1`` の読みにも影響しないので判定に使わない。
    """

    return any(
        Path(f"{db_path}{suffix}").exists() and Path(f"{db_path}{suffix}").stat().st_size > 0
        for suffix in ("-wal", "-journal")
    )


def _copy_quiescent_family(source: Path, destination: Path) -> None:
    """writer を締め出したまま family を複製する。

    Windows では複製の間ずっと deny-write handle を保持するので、**torn copy が構造的に
    起こり得ない**。他 OS ではその API が無いため、複製の前後で family の同一性を比較する
    fallback を使う。fallback は writer が動いていることの検出であって、静止していることの
    証明ではない。
    """

    for attempt in range(1, _COPY_ATTEMPT_LIMIT + 1):
        try:
            with _deny_write(source) as is_exclusive:
                before = None if is_exclusive else _family_identity(source)
                for suffix in _COPIED_SUFFIXES:
                    source_member = Path(f"{source}{suffix}")
                    destination_member = Path(f"{destination}{suffix}")
                    if suffix and not source_member.exists():
                        destination_member.unlink(missing_ok=True)
                        continue
                    copyfile(source_member, destination_member)
                if before is None or _family_identity(source) == before:
                    return
        except ArchiveDatabaseLivenessError:
            if attempt == _COPY_ATTEMPT_LIMIT:
                raise
        logger.info("Archive database %s was busy while being copied; retrying (%d)", source, attempt)
        # ウイルス対策やインデクサが一瞬掴んでいるだけのことがある。間を空けずに
        # 再試行すると 3 回がマイクロ秒で消化され、retry の意味が無い。
        time.sleep(_COPY_RETRY_BACKOFF_S)
    raise ArchiveDatabaseLivenessError(_LIVENESS_MESSAGE.format(source=source))


_LIVENESS_MESSAGE: Final = (
    "Archive database {source} is held by a writer, so this run appears to be still executing. "
    "Use the dashboard of the running process, or stop the run before opening its directory as "
    "an archive."
)


@contextmanager
def _deny_write(db_path: Path) -> Iterator[bool]:
    """複製の間 writer を締め出す。締め出せたかどうかを yield する。

    Windows 以外では締め出す手段が無いので ``False`` を yield し、呼び出し側の
    同一性比較 fallback に任せる。
    """

    if sys.platform != "win32":
        yield False
        return

    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle = wintypes.HANDLE(-1).value
    error_sharing_violation = 32
    error_lock_violation = 33

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handles: list[int] = []
    try:
        for suffix in _COPIED_SUFFIXES:
            member = Path(f"{db_path}{suffix}")
            if suffix and not member.exists():
                continue
            handle = kernel32.CreateFileW(
                str(member),
                generic_read,
                file_share_read,
                None,
                open_existing,
                file_attribute_normal,
                None,
            )
            if handle in (invalid_handle, None):
                # 共有違反だけを「writer が居る」と解釈する。権限不足やパス長まで
                # 同じ結論にすると、存在しない writer を探させることになる。
                # それはこの task が直している誤誘導の再生産である。
                code = ctypes.get_last_error()
                if code in (error_sharing_violation, error_lock_violation):
                    raise ArchiveDatabaseLivenessError(_LIVENESS_MESSAGE.format(source=db_path))
                raise ArchiveDatabaseSnapshotError(
                    f"Unable to open the archive database {member} for copying "
                    f"(Windows error {code}: {ctypes.FormatError(code)})."
                )
            handles.append(handle)
        yield True
    finally:
        for handle in handles:
            kernel32.CloseHandle(wintypes.HANDLE(handle))


def _family_identity(db_path: Path) -> tuple[tuple[str, int, int, str], ...]:
    identity: list[tuple[str, int, int, str]] = []
    for suffix in _COPIED_SUFFIXES:
        member = Path(f"{db_path}{suffix}")
        if not member.exists():
            continue
        metadata = member.stat()
        # 本体は GB 級になり得るのでハッシュしない。sidecar は小さく、WAL journal mode では
        # commit のたびに ``-wal`` が伸びるため、この組み合わせで動いている writer を検出できる。
        digest = _chunked_digest(member) if suffix else ""
        identity.append((suffix, metadata.st_size, metadata.st_mtime_ns, digest))
    return tuple(identity)


def _chunked_digest(path: Path) -> str:
    """sidecar のハッシュを取る。crash 後の ``-wal`` は数百 MB になり得る。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _fold_sidecars(db_path: Path) -> None:
    """複製側で WAL を本体へ畳み、hot journal を rollback して sidecar を消す。

    ``journal_mode=DELETE`` は WAL の checkpoint と ``-wal`` / ``-shm`` の削除を兼ねる。
    hot な rollback journal は writable open の時点で回復される。

    失敗したら例外にする。原本の ``immutable=1`` へ黙って戻すのは、この task が直している
    無音の読み落としそのものになる。
    """

    try:
        connection = sqlite3.connect(db_path, timeout=5.0)
    except sqlite3.DatabaseError as exc:
        raise ArchiveDatabaseSnapshotError(f"Unable to open the archive snapshot {db_path}: {exc}") from exc
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        raise ArchiveDatabaseSnapshotError(
            f"Unable to fold the write-ahead log of the archive snapshot {db_path}: {exc}"
        ) from exc
    finally:
        connection.close()

    remaining = [suffix for suffix in SIDECAR_SUFFIXES if Path(f"{db_path}{suffix}").exists()]
    if remaining:
        raise ArchiveDatabaseSnapshotError(
            f"The archive snapshot {db_path} still has {', '.join(remaining)} after folding its "
            "write-ahead log; refusing to serve it because committed rows could be dropped silently."
        )


__all__ = [
    "SIDECAR_SUFFIXES",
    "ArchiveDatabaseSnapshot",
    "materialize_quiescent_database",
    "resolve_archive_databases",
]
