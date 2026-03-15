"""Runtime models for remote instances."""

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

from .active_game import InstanceActiveGame, InstanceActiveGameSide
from .instance_config_models import (
    InstanceConfig,
    InstanceType,
)

logger = logging.getLogger(__name__)


@dataclass
class InstanceMetrics:
    """Runtime metrics for an instance."""

    sampled_ts_sec: float = field(default_factory=time.time)
    is_reachable: bool = True
    load_avg_1: float | None = None
    load_avg_5: float | None = None
    load_avg_15: float | None = None
    cpu_usage_pct: float | None = None
    cpu_model: str | None = None
    cpu_count: int | None = None
    cpu_usage_pct_per_core: list[float] | None = None
    mem_total_mb: int | None = None
    mem_free_mb: int | None = None
    mem_used_mb: int | None = None
    mem_used_pct: float | None = None
    in_use_slots: int = 0
    in_use_engines: int = 0
    engine_processes: int = 0
    latency_avg_ms: float | None = None
    latency_recent_ms: float | None = None
    latency_samples: int = 0
    has_latency_alert: bool = False
    latency_threshold_ms: int | None = None
    network_rtt_avg_ms: float | None = None
    network_rtt_recent_ms: float | None = None
    network_rtt_samples: int = 0


@dataclass
class Instance:
    """Runtime instance with configuration and state."""

    config: InstanceConfig
    metrics: InstanceMetrics = field(default_factory=InstanceMetrics)
    is_draining: bool = False
    last_seen_ts_sec: float = field(default_factory=time.time)
    active_game_by_id: dict[str, InstanceActiveGame] = field(default_factory=dict)
    source_path: Path | None = None
    _network_rtt_samples: deque[float] = field(default_factory=lambda: deque(maxlen=120), repr=False)
    _network_rtt_sum: float = field(default=0.0, repr=False)
    _network_probe_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.is_local and self.metrics.cpu_count is None:
            count = os.cpu_count()
            if isinstance(count, int) and count > 0:
                self.metrics.cpu_count = count

    @property
    def name(self) -> str:
        """Instance name."""
        return self.config.name

    @property
    def type(self) -> InstanceType:
        """Instance type."""
        return self.config.type

    @property
    def is_local(self) -> bool:
        """Whether this is a local instance."""
        return self.config.type == InstanceType.LOCAL

    @property
    def is_ssh(self) -> bool:
        """Whether this is an SSH instance."""
        return self.config.type == InstanceType.SSH

    def _slot_limit(self) -> int | None:
        """Resolve effective slot capacity (config or auto)."""
        raw_slots = self.config.slots
        limit = int(raw_slots) if raw_slots is not None else 0
        if limit > 0:
            return limit
        metrics_cap = self.metrics.cpu_count
        if isinstance(metrics_cap, int) and metrics_cap > 0:
            return metrics_cap
        return None

    @property
    def effective_slots(self) -> int | None:
        """Expose resolved slot capacity for reporting."""
        return self._slot_limit()

    @property
    def available_slots(self) -> int:
        """Available slots for new jobs."""
        if self.is_draining:
            return 0
        limit = self._slot_limit()
        if limit is None:
            return 0
        return max(0, limit - self.metrics.in_use_slots)

    @property
    def max_engine_capacity(self) -> int:
        """Maximum concurrent engine processes allowed on this instance."""
        if self.config.max_engines is not None and self.config.max_engines > 0:
            return self.config.max_engines
        limit = self._slot_limit()
        return 0 if limit is None else limit

    @property
    def available_engines(self) -> int:
        """Available engine slots for new jobs."""
        if self.is_draining:
            return 0
        limit = self.max_engine_capacity
        return max(0, limit - self.metrics.in_use_engines)

    @property
    def is_slot_capacity_known(self) -> bool:
        return self._slot_limit() is not None

    @property
    def is_engine_capacity_known(self) -> bool:
        if self.config.max_engines is not None and self.config.max_engines > 0:
            return True
        return self._slot_limit() is not None

    @property
    def can_accept_job(self) -> bool:
        """Whether instance can accept new jobs."""
        if not self.metrics.is_reachable or self.is_draining:
            return False
        if not self.is_slot_capacity_known:
            return False
        slot_limit = self._slot_limit()
        if slot_limit is not None and self.metrics.in_use_slots >= slot_limit:
            return False
        engine_limit = self.max_engine_capacity
        if self.metrics.in_use_engines >= engine_limit:
            return False
        return True

    def try_acquire_resources(self, *, slots: int = 0, engines: int = 0) -> bool:
        """
        Try to acquire slots and/or engine capacity for a new job.

        Args:
            slots: Number of CPU slots required
            engines: Number of engine processes required

        Returns:
            True if resources were acquired, False otherwise.
        """
        if slots < 0 or engines < 0:
            raise ValueError("resource counts must be >= 0")
        if slots == 0 and engines == 0:
            return True
        if not self.metrics.is_reachable or self.is_draining:
            return False

        if not self.is_slot_capacity_known:
            return False
        slot_limit = self._slot_limit()
        if slots and slot_limit is not None and (self.metrics.in_use_slots + slots) > slot_limit:
            return False

        if engines and not self.is_engine_capacity_known:
            return False
        engine_limit = self.max_engine_capacity
        if engines and (self.metrics.in_use_engines + engines) > engine_limit:
            return False
        self.metrics.in_use_slots += slots
        self.metrics.in_use_engines += engines
        return True

    def release_resources(self, *, slots: int = 0, engines: int = 0) -> None:
        """Release slots and/or engine capacity after job completion."""
        if slots < 0 or engines < 0:
            raise ValueError("resource counts must be >= 0")
        if slots:
            if slots >= self.metrics.in_use_slots:
                self.metrics.in_use_slots = 0
            else:
                self.metrics.in_use_slots -= slots
        if engines:
            if engines >= self.metrics.in_use_engines:
                self.metrics.in_use_engines = 0
            else:
                self.metrics.in_use_engines -= engines

    def add_engine_processes(self, delta: int = 1) -> None:
        """Track actual engine processes created on this instance."""
        if delta <= 0:
            return
        self.metrics.engine_processes += int(delta)

    def remove_engine_processes(self, delta: int = 1) -> None:
        """Track actual engine processes removed on this instance."""
        if delta <= 0:
            return
        if delta >= self.metrics.engine_processes:
            self.metrics.engine_processes = 0
        else:
            self.metrics.engine_processes -= int(delta)

    def update_metrics(self, new_metrics: InstanceMetrics) -> None:
        """Update instance metrics."""
        # Preserve in_use_slots when updating metrics
        current_slots = self.metrics.in_use_slots
        current_engines = self.metrics.in_use_engines
        current_processes = self.metrics.engine_processes
        self.metrics = new_metrics
        self.metrics.in_use_slots = current_slots
        self.metrics.in_use_engines = current_engines
        self.metrics.engine_processes = current_processes

        if new_metrics.is_reachable:
            self.last_seen_ts_sec = time.time()

    def record_network_rtt(self, rtt_ms: float) -> None:
        """Record a round-trip-time sample captured via SSH probe."""

        samples = self._network_rtt_samples
        if samples.maxlen and len(samples) >= samples.maxlen:
            oldest = samples.popleft()
            self._network_rtt_sum -= oldest

        samples.append(rtt_ms)
        self._network_rtt_sum += rtt_ms

        self.metrics.network_rtt_recent_ms = float(rtt_ms)
        self.metrics.network_rtt_samples = len(samples)
        if samples:
            self.metrics.network_rtt_avg_ms = self._network_rtt_sum / len(samples)
        else:
            self.metrics.network_rtt_avg_ms = None

    def start_network_probe(self, conn: asyncssh.SSHClientConnection, *, interval: float = 1.0) -> asyncio.Task[None]:
        """Start an SSH RTT probe loop for this instance."""

        if not self.is_ssh:
            raise ValueError("Network probe is only supported for SSH instances")

        loop = asyncio.get_running_loop()
        task = loop.create_task(self._network_probe_loop(conn, interval), name=f"rtt-probe-{self.name}")

        self._network_probe_tasks.add(task)

        def _cleanup(done: asyncio.Task[None]) -> None:
            self._network_probe_tasks.discard(done)

        task.add_done_callback(_cleanup)
        return task

    async def stop_network_probe(self, task: asyncio.Task[None] | None) -> None:
        """Cancel and await a running probe task."""

        if task is None:
            return
        if task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def cancel_network_probe(self, task: asyncio.Task[None] | None) -> None:
        """Cancel a running probe task without awaiting completion."""

        if task is None:
            return
        task.cancel()

    async def _network_probe_loop(self, conn: asyncssh.SSHClientConnection, interval: float) -> None:
        """Continuously issue lightweight commands to estimate SSH RTT."""

        while True:
            if self._connection_closed(conn):
                return
            try:
                start = time.perf_counter()
                result = await asyncio.wait_for(conn.run("printf ok"), timeout=5.0)
                duration_ms = (time.perf_counter() - start) * 1000.0
                if result.exit_status == 0:
                    self.record_network_rtt(duration_ms)
                else:
                    logger.debug(
                        "SSH RTT probe returned non-zero exit (instance=%s, exit=%s)",
                        self.name,
                        result.exit_status,
                    )
            except asyncio.CancelledError:
                raise
            except (TimeoutError, OSError, RuntimeError) as exc:
                logger.debug("SSH RTT probe failed for %s: %s", self.name, exc, exc_info=True)
                if self._connection_closed(conn):
                    return
            await asyncio.sleep(interval)

    @staticmethod
    def _connection_closed(conn: asyncssh.SSHClientConnection) -> bool:
        """Best-effort check to see if an asyncssh connection is closing."""

        for attr in ("is_closing", "closing", "closed"):
            value = getattr(conn, attr, None)
            if callable(value):
                try:
                    if value():
                        return True
                except (RuntimeError, TypeError):
                    return True
            elif value:
                return True
        return False


__all__ = [
    "Instance",
    "InstanceActiveGame",
    "InstanceActiveGameSide",
    "InstanceConfig",
    "InstanceMetrics",
    "InstanceType",
]
