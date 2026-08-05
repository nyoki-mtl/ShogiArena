"""アーカイブ DB の読み取り先を解決する契約。

実装は ``contexts/dashboard/adapters/archive_snapshot.py``。規約は
``agent-docs/rules/archive-read-contract.md`` を参照。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol

GAME_DB_RELATIVE_PATH: Final = Path("game.db")
"""run ディレクトリからの対局 DB の相対パス。"""


class ArchiveDatabaseSnapshotError(RuntimeError):
    """アーカイブ DB を安全に読める形へ解決できなかった。"""


class ArchiveDatabaseLivenessError(ArchiveDatabaseSnapshotError):
    """DB を writer が掴んでおり、静止した複製を作れない。"""


class ArchiveDatabaseSnapshotPort(Protocol):
    """アーカイブの DB をどこから開くかの解決結果。"""

    def database_dir_for(self, relative_database_path: Path) -> Path: ...

    def database_path_for(self, relative_database_path: Path) -> Path: ...

    @property
    def materialized_database_paths(self) -> tuple[Path, ...]: ...

    def close(self) -> None: ...

    def __enter__(self) -> ArchiveDatabaseSnapshotPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class ArchiveDatabaseResolverFn(Protocol):
    """回復すべき sidecar を持つ DB だけを安全に読める形へ解決する。"""

    def __call__(
        self,
        run_dir: Path,
        *,
        relative_database_paths: Sequence[Path],
    ) -> ArchiveDatabaseSnapshotPort: ...


__all__ = [
    "GAME_DB_RELATIVE_PATH",
    "ArchiveDatabaseLivenessError",
    "ArchiveDatabaseResolverFn",
    "ArchiveDatabaseSnapshotError",
    "ArchiveDatabaseSnapshotPort",
]
