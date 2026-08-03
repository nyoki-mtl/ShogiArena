"""Off-loop scheduling for SPSA derived JSON projection.

``project_spsa_ledger`` rebuilds ``current.json`` / ``index.json`` / ``events.jsonl``
from the whole run, so its cost grows with run length. Calling it inline from the
game-completion or update-commit path blocks the asyncio loop that also serves the
dashboard, and the stall grows as the run progresses.

This scheduler keeps the projection off the loop and coalesces bursts:

- ``request()`` schedules a debounced background projection and returns immediately.
- ``drain()`` stops accepting new work and awaits whatever is in flight.
- ``project_blocking()`` runs one synchronous projection, for run start and terminal.

Background projections run on a worker thread against a dedicated read-only ledger
connection, so they never touch the writer connection owned by the run.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

from shogiarena._core.contexts.spsa.adapters.ledger_projection import project_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedger, open_spsa_ledger
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH

logger = logging.getLogger(__name__)

# 派生 JSON は run 全体から再生成されるため、1 回のコストは run 長に比例する。
# ledger が権威であり、dashboard は projector 経由で ledger を直接読むので、
# この互換ビューに要求される鮮度は「最終的に追いつくこと」だけである。
# 投影を頻繁に走らせると worker thread を長く占有し、同じ worker pool を使う
# REST ハンドラの応答遅延の裾を押し上げる。実測ではこの間隔を 30 秒にすると
# summary の p90 が 74ms から 474ms へ悪化した。
DEFAULT_MIN_INTERVAL_S = 300.0


_PROJECTION_LOG_THRESHOLD_MS = 500.0


class SpsaDerivedJsonScheduler:
    """Coalesce SPSA derived JSON projections and run them off the event loop.

    ``request()`` は event loop 上の同期コードから呼んでよい。実際の投影は worker
    thread で走り、``min_interval_s`` の間隔で束ねられる。run 開始と terminal では
    ``project_blocking()`` で確定的に書き出す。
    """

    def __init__(
        self,
        *,
        run_dir: Path,
        run_id: str,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._min_interval_s = max(0.0, min_interval_s)
        self._ledger: SpsaLedger | None = None
        self._task: asyncio.Task[None] | None = None
        self._skip_debounce = asyncio.Event()
        self._is_pending = False
        self._is_accepting = True
        self._last_projected_s: float | None = None

    def request(self) -> None:
        """Schedule a debounced projection without blocking the caller.

        event loop が動いていない場合は何もしない。確実な書き出しが必要な呼び出し元は
        :meth:`project_blocking` を使う。
        """

        if not self._is_accepting:
            return
        if self._task is not None and not self._task.done():
            self._is_pending = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No running event loop; skipping SPSA derived JSON projection request")
            return
        self._task = loop.create_task(self._run_debounced())

    async def drain(self) -> None:
        """Stop accepting new requests and finish the scheduled projection.

        待機中の debounce は打ち切るが、既に要求済みの投影は破棄せず完了させる。
        """

        self._is_accepting = False
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        self._skip_debounce.set()
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - teardown must survive projection failure
            logger.debug("SPSA derived JSON projection task failed during drain: %s", exc, exc_info=True)

    def project_blocking(self) -> None:
        """Project synchronously. Run start と terminal でのみ使う。"""

        started = time.perf_counter()
        self._project(self._resolve_connection())
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms >= _PROJECTION_LOG_THRESHOLD_MS:
            logger.info("SPSA derived JSON projection took %.0fms", elapsed_ms)
        self._last_projected_s = time.monotonic()

    def close(self) -> None:
        """Release the scheduler-owned read-only ledger connection."""

        self._is_accepting = False
        ledger = self._ledger
        self._ledger = None
        if ledger is not None:
            try:
                ledger.close()
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                logger.debug("Failed to close SPSA projection ledger connection: %s", exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _run_debounced(self) -> None:
        while True:
            self._is_pending = False
            await self._wait_for_debounce()
            await self._project_off_loop()
            if not self._is_pending:
                return

    async def _wait_for_debounce(self) -> None:
        delay = self._remaining_debounce_s()
        if delay <= 0 or self._skip_debounce.is_set():
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._skip_debounce.wait(), timeout=delay)

    def _remaining_debounce_s(self) -> float:
        if self._last_projected_s is None:
            return 0.0
        elapsed = time.monotonic() - self._last_projected_s
        return max(0.0, self._min_interval_s - elapsed)

    async def _project_off_loop(self) -> None:
        try:
            await asyncio.to_thread(self.project_blocking)
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("SPSA ledger remains authoritative after derived JSON projection failure: %s", exc)
            self._last_projected_s = time.monotonic()

    def _project(self, connection: sqlite3.Connection) -> None:
        project_spsa_ledger(
            connection=connection,
            run_id=self._run_id,
            run_dir=self._run_dir,
        )

    def _resolve_connection(self) -> sqlite3.Connection:
        ledger = self._ledger
        if ledger is not None:
            return ledger.connection
        ledger_path = self._run_dir / SPSA_LEDGER_RELATIVE_PATH
        if not ledger_path.is_file():
            raise ValueError(f"SPSA ledger is unavailable for projection: {ledger_path}")
        opened = open_spsa_ledger(self._run_dir, read_only=True)
        self._ledger = opened
        return opened.connection


__all__ = ["DEFAULT_MIN_INTERVAL_S", "SpsaDerivedJsonScheduler"]
