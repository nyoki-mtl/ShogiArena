"""Production event-loop / thread stall watchdog（task 0047、0052 で coverage 対応）。

orchestrator の停止を検知し、後段の timeout attribution が「この手の時間窓に loop stall が
重なったか」と「その窓を実際に観測できていたか」を問い合わせられるようにする。

2 系統を持つ:

- **loop heartbeat**: coroutine。``asyncio.sleep`` の overshoot を event loop lag として測る。
- **thread overshoot**: daemon thread。``Event.wait`` の overshoot を測る。GIL を保持した
  pure-Python 停滞（loop heartbeat が動けないケース）も捉える。

両系統とも ``time.perf_counter`` を時間基底にする（``GameClock`` と同一のため move window と
突き合わせ可能）。lag event は ``[start_s, end_s]`` 区間とその大きさを ring buffer に保持する。

ring は有限なので、長時間の run では古い event が押し出される。押し出しは「lag が無かった」
ことを意味しないため、coverage frontier と dropped 件数を明示的に追跡し、query 窓が
frontier より前に始まる場合は ``coverage_complete=False`` を返す（review finding M4）。

summary も attribution と同じ関数で in-flight lag を合成する。記録済み event だけを集計すると、
無効局があるのに artifact 上は lag なしと表示できてしまう（review finding M5）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


DEFAULT_INTERVAL_S = 0.05
DEFAULT_LOOP_THRESHOLD_MS = 200.0
# Windows のタイマ粒度（~15.6ms）を踏まえ 100ms 以上にする。
DEFAULT_THREAD_THRESHOLD_MS = 200.0
# ring は「直近の move window を覆えるだけの長さ」があればよい。
# 0052 の shadow 計測では、停滞が続く条件の 300 局 run で 559 event が発生し、
# 256 では 303 event を押し出して coverage 不完全になった（＝以降の timeout が軒並み unknown）。
# heartbeat 間隔 50ms 換算で 4096 event は数分ぶんの停滞を保持でき、
# LagEvent は 1 件あたり数十バイトなのでメモリ影響も小さい。
DEFAULT_RING_SIZE = 4096


@dataclass(frozen=True, slots=True)
class LagEvent:
    """1 回の stall。``[start_s, end_s]`` は perf_counter 秒、magnitude はその ms。"""

    channel: str  # "loop" | "thread"
    start_s: float
    end_s: float
    magnitude_ms: float


@dataclass(frozen=True, slots=True)
class LoopLagObservation:
    """query 窓に対する loop lag の観測結果。

    値だけでなく「その窓を観測できていたか」を返すため、attribution は
    「lag なし」と「証拠が失われた」を区別できる。
    """

    lag_ms_in_window: float
    """記録済み event と in-flight lag の合計（ms）。"""

    in_flight_lag_ms: float
    """query 時点でまだ記録されていない進行中の停滞（ms）。上の合計に含まれる。"""

    is_available: bool
    """watchdog が観測を提供できるか。未起動・停止後で確定 snapshot が無い場合は False。"""

    coverage_complete: bool
    """窓の全区間を観測できていたか。ring overflow / restart / 起動前は False。"""

    watchdog_generation: int
    """``start()`` ごとに増える世代。窓が世代をまたぐと coverage は不完全。"""

    dropped_event_count: int
    """ring から押し出された loop event の累計。"""

    dropped_through_s: float | None
    """押し出した event の coverage frontier。これより前の窓は観測できていない。"""


@runtime_checkable
class LoopLagProbePort(Protocol):
    """timeout attribution が必要とする最小契約（``RuntimeWatchdog`` が構造的に満たす）。"""

    def observe_loop_lag(self, start_s: float, end_s: float) -> LoopLagObservation: ...


@dataclass(frozen=True, slots=True)
class WatchdogSummary:
    """run の run-health artifact 向けの集計。"""

    loop_lag_events: int
    thread_lag_events: int
    max_loop_lag_ms: float
    max_thread_lag_ms: float
    loop_threshold_ms: float
    thread_threshold_ms: float
    dropped_loop_events: int = 0
    is_coverage_complete: bool = True


@runtime_checkable
class WatchdogSummaryPort(Protocol):
    """run-health artifact が必要とする最小契約（``RuntimeWatchdog`` が構造的に満たす）。"""

    def summary(self) -> WatchdogSummary: ...


class RuntimeWatchdog:
    """event loop / thread の停滞を常時監視し、lag event を ring に記録する。"""

    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        loop_threshold_ms: float = DEFAULT_LOOP_THRESHOLD_MS,
        thread_threshold_ms: float = DEFAULT_THREAD_THRESHOLD_MS,
        ring_size: int = DEFAULT_RING_SIZE,
    ) -> None:
        self._interval_s = interval_s
        self._loop_threshold_ms = loop_threshold_ms
        self._thread_threshold_ms = thread_threshold_ms
        # ``_lock`` は daemon thread と event loop 間で ring / last-beat を守る。
        self._lock = threading.Lock()
        self._events: deque[LagEvent] = deque(maxlen=max(1, ring_size))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._loop_last_beat_s: float | None = None
        self._max_loop_lag_ms = 0.0
        self._max_thread_lag_ms = 0.0
        self._loop_lag_events = 0
        self._thread_lag_events = 0
        # --- coverage tracking (task 0052) ---
        self._generation = 0
        self._generation_started_at_s: float | None = None
        self._dropped_loop_events = 0
        self._dropped_through_s: float | None = None
        self._is_running = False

    def start(self) -> None:
        """daemon thread と loop heartbeat task を起動する（running loop 上で呼ぶこと）。"""
        now = time.perf_counter()
        with self._lock:
            # restart は新しい世代にする。世代をまたぐ窓は coverage 不完全として扱う。
            self._generation += 1
            self._generation_started_at_s = now
            self._is_running = True
            self._stop_event.clear()
        if self._thread is None:
            self._thread = threading.Thread(target=self._thread_main, name="runtime-watchdog", daemon=True)
            self._thread.start()
        if self._loop_task is None:
            with self._lock:
                self._loop_last_beat_s = now
            self._loop_task = asyncio.get_running_loop().create_task(
                self._loop_heartbeat(), name="runtime-watchdog-loop"
            )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._loop_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._is_running = False

    def _record(self, event: LagEvent) -> None:
        with self._lock:
            self._record_locked(event)

    def _record_locked(self, event: LagEvent) -> None:
        events = self._events
        if len(events) == events.maxlen:
            # deque は最古を黙って捨てる。捨てた区間は「lag なし」ではなく「観測不能」なので
            # frontier を進めて後段の coverage 判定に使う。
            evicted = events[0]
            if evicted.channel == "loop":
                self._dropped_loop_events += 1
            frontier = self._dropped_through_s
            self._dropped_through_s = evicted.end_s if frontier is None else max(frontier, evicted.end_s)
        events.append(event)
        if event.channel == "loop":
            self._loop_lag_events += 1
            self._max_loop_lag_ms = max(self._max_loop_lag_ms, event.magnitude_ms)
        else:
            self._thread_lag_events += 1
            self._max_thread_lag_ms = max(self._max_thread_lag_ms, event.magnitude_ms)

    async def _loop_heartbeat(self) -> None:
        # Anchor ``expected`` to the start-time beat, not to when this coroutine first runs: a stall
        # that begins before the first tick (e.g. a blocking call right after start) must still be seen.
        with self._lock:
            anchor = self._loop_last_beat_s
        expected = (anchor if anchor is not None else time.perf_counter()) + self._interval_s
        while not self._stop_event.is_set():
            await asyncio.sleep(max(0.0, expected - time.perf_counter()))
            now = time.perf_counter()
            with self._lock:
                self._loop_last_beat_s = now
            lag_ms = max(0.0, (now - expected) * 1000.0)
            if lag_ms >= self._loop_threshold_ms:
                self._record(LagEvent(channel="loop", start_s=expected, end_s=now, magnitude_ms=lag_ms))
                logger.warning("Event loop stalled for %.0fms", lag_ms)
            expected = max(expected + self._interval_s, now)

    def _thread_main(self) -> None:
        expected = time.perf_counter() + self._interval_s
        while not self._stop_event.wait(max(0.0, expected - time.perf_counter())):
            now = time.perf_counter()
            overshoot_ms = max(0.0, (now - expected) * 1000.0)
            if overshoot_ms >= self._thread_threshold_ms:
                self._record(LagEvent(channel="thread", start_s=expected, end_s=now, magnitude_ms=overshoot_ms))
                logger.warning("Watchdog thread overshoot %.0fms (process stalled)", overshoot_ms)
            expected = max(expected + self._interval_s, now)

    def _in_flight_lag_locked(self, *, now: float, start_s: float, end_s: float) -> float:
        """まだ記録されていない進行中の停滞のうち、窓に重なる分（ms）。

        attribution と summary の双方でこの関数だけを使い、合成規則を一箇所に保つ。
        """
        last_beat = self._loop_last_beat_s
        if last_beat is None or not self._is_running:
            # 停止後は heartbeat が進まないので、経過時間を停滞として数えない。
            return 0.0
        in_flight_start = last_beat + self._interval_s
        overlap_s = min(now, end_s) - max(in_flight_start, start_s)
        return overlap_s * 1000.0 if overlap_s > 0.0 else 0.0

    def observe_loop_lag(self, start_s: float, end_s: float) -> LoopLagObservation:
        """``[start_s, end_s]`` の loop stall と、その窓の coverage を返す（attribution 用）。

        ring から event が押し出された区間、watchdog 起動前、restart をまたぐ窓は
        ``coverage_complete=False`` になる。値 0 と「証拠が失われた」を混同しない。
        """
        now = time.perf_counter()
        with self._lock:
            generation = self._generation
            generation_started_at_s = self._generation_started_at_s
            dropped_event_count = self._dropped_loop_events
            dropped_through_s = self._dropped_through_s
            is_running = self._is_running
            recorded_ms = 0.0
            if end_s > start_s:
                for event in self._events:
                    if event.channel != "loop":
                        continue
                    overlap = min(event.end_s, end_s) - max(event.start_s, start_s)
                    if overlap > 0.0:
                        recorded_ms += overlap * 1000.0
            in_flight_ms = self._in_flight_lag_locked(now=now, start_s=start_s, end_s=end_s) if end_s > start_s else 0.0

        if generation_started_at_s is None:
            # 一度も起動していない。watchdog 不在は「lag なし」ではなく観測不能。
            return LoopLagObservation(
                lag_ms_in_window=0.0,
                in_flight_lag_ms=0.0,
                is_available=False,
                coverage_complete=False,
                watchdog_generation=generation,
                dropped_event_count=dropped_event_count,
                dropped_through_s=dropped_through_s,
            )

        coverage_complete = True
        if not is_running:
            # stop 後の query は最後の確定 snapshot として値は返すが、完全性は主張しない。
            coverage_complete = False
        if start_s < generation_started_at_s:
            # 窓が現世代の起動より前に始まる（未起動区間、または restart をまたいだ）。
            coverage_complete = False
        if dropped_through_s is not None and start_s < dropped_through_s:
            coverage_complete = False

        return LoopLagObservation(
            lag_ms_in_window=recorded_ms + in_flight_ms,
            in_flight_lag_ms=in_flight_ms,
            is_available=True,
            coverage_complete=coverage_complete,
            watchdog_generation=generation,
            dropped_event_count=dropped_event_count,
            dropped_through_s=dropped_through_s,
        )

    def summary(self) -> WatchdogSummary:
        """artifact 向けの集計。進行中の停滞も反映する（review finding M5）。"""
        now = time.perf_counter()
        with self._lock:
            # 進行中の停滞は「開始からいまこの瞬間まで」を窓として合成する。
            in_flight_ms = self._in_flight_lag_locked(now=now, start_s=float("-inf"), end_s=now)
            return WatchdogSummary(
                loop_lag_events=self._loop_lag_events,
                thread_lag_events=self._thread_lag_events,
                max_loop_lag_ms=max(self._max_loop_lag_ms, in_flight_ms),
                max_thread_lag_ms=self._max_thread_lag_ms,
                loop_threshold_ms=self._loop_threshold_ms,
                thread_threshold_ms=self._thread_threshold_ms,
                dropped_loop_events=self._dropped_loop_events,
                is_coverage_complete=self._dropped_loop_events == 0,
            )


__all__ = [
    "LagEvent",
    "LoopLagObservation",
    "LoopLagProbePort",
    "RuntimeWatchdog",
    "WatchdogSummary",
    "WatchdogSummaryPort",
]
