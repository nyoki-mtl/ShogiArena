"""対局中のledger更新を専用threadとconnectionで直列実行する。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from shogiarena._core.contexts.spsa.adapters.ledger_observations import SpsaObservationLedger
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedger, open_spsa_ledger
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerGameObservation
from shogiarena._core.shared.kernel.async_completion import complete_before_cancelling

_T = TypeVar("_T")


class SpsaLiveLedgerWriter:
    """Connectionの生成、更新、解放を同じthreadで行う。"""

    def __init__(self, *, run_dir: Path, run_id: str) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spsa-ledger")
        self._ledger: SpsaLedger | None = None
        self._is_closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _connection(self) -> SpsaLedger:
        if self._ledger is None:
            # The session owns WAL/checkpoint policy. This connection only commits
            # live observations/quarantine; update transitions await all game tasks.
            self._ledger = open_spsa_ledger(self._run_dir)
        return self._ledger

    async def _run(self, operation: Callable[[], _T]) -> _T:
        if self._is_closed:
            raise RuntimeError("SPSA live ledger writer is closed")
        result = asyncio.get_running_loop().run_in_executor(self._executor, operation)
        return await complete_before_cancelling(result)

    async def insert_committed(self, observation: LedgerGameObservation) -> bool:
        """終局観測のcommitをloop外で完了する。"""
        return await self._run(
            lambda: SpsaObservationLedger(self._connection().connection).insert_committed(observation)
        )

    async def record_variant_quarantine(
        self, *, update_idx: int, pair_id: str, variant_id: str, failure_classification: str
    ) -> None:
        """隔離記録を観測と同じwriterでcommitする。"""
        await self._run(
            lambda: SpsaLedgerRuntime(self._connection().connection, run_id=self._run_id).record_variant_quarantine(
                update_idx=update_idx,
                pair_id=pair_id,
                variant_id=variant_id,
                failure_classification=failure_classification,
            )
        )

    async def close(self) -> None:
        """保存を回収してからconnectionとexecutorを閉じる。"""

        def close_connection() -> None:
            if self._ledger is not None:
                self._ledger.close()
                self._ledger = None

        async def close_owned() -> None:
            try:
                await asyncio.get_running_loop().run_in_executor(self._executor, close_connection)
            finally:
                await asyncio.to_thread(self._executor.shutdown, wait=True)

        if self._close_task is None:
            self._is_closed = True
            self._close_task = asyncio.create_task(close_owned())
        await complete_before_cancelling(self._close_task)
