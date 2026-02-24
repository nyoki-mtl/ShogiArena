"""Data models for remote instances configuration."""

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import asyncssh

from shogiarena.utils.types.types import JsonObject

logger = logging.getLogger(__name__)


class InstanceType(str, Enum):
    """Types of instances supported."""

    LOCAL = "local"
    SSH = "ssh"


@dataclass
class InstanceConfig:
    """Configuration for an instance from YAML.

    For SSH instances, `project_root` defines the remote project root directory
    (defaults to `$HOME/ShogiArena-remote`). `engine_dir` is derived as
    `{project_root}/data/engines` and should not be specified directly in YAML.
    """

    name: str
    type: InstanceType
    # Derived for SSH as {project_root}/data/engines; must be empty for LOCAL
    engine_dir: str
    # SSH-only optional override of project root; if empty, defaults to ~/ShogiArena-remote
    project_root: str = ""
    host: str | None = None
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    # slots is None means "auto" (resolved from metrics when available)
    slots: int | None = None
    max_engines: int | None = None
    tags: list[str] = field(default_factory=list)
    strict_host_key_checking: bool = True
    install_requirements: bool = False

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.name or not self.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError("Instance name must be alphanumeric with hyphens, underscores, or dots")

        if self.max_engines is not None and not isinstance(self.max_engines, int):
            raise TypeError("max_engines must be an integer when provided")

        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if isinstance(self.slots, bool):
            raise TypeError("slots must be an integer or null")
        if self.slots is None:
            pass
        elif not isinstance(self.slots, int):
            raise TypeError("slots must be an integer or null")
        elif self.slots <= 0:
            raise ValueError("slots must be positive or null (null means auto)")

        # Derive engine_dir for SSH from project_root when present
        if self.type == InstanceType.LOCAL:
            if self.engine_dir:
                raise ValueError("engine_dir must not be set for local instances")
            # Ignore project_root for local
            self.project_root = ""
        elif self.type == InstanceType.SSH:
            # Normalize project_root default
            if not self.project_root:
                # Default to remote home (do not expand locally)
                self.project_root = "$HOME/ShogiArena-remote"
            # Derive engine_dir from project_root consistently
            self.engine_dir = str(Path(self.project_root) / "data" / "engines")

    def model_dump(self) -> JsonObject:
        """Convert to dictionary (compatibility method)."""
        return {
            "name": self.name,
            "type": self.type.value,
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "identity_file": self.identity_file,
            "engine_dir": self.engine_dir,
            "project_root": self.project_root,
            "slots": self.slots,
            "max_engines": self.max_engines,
            "tags": self.tags,
            "strict_host_key_checking": self.strict_host_key_checking,
            "install_requirements": self.install_requirements,
        }


@dataclass
class InstanceMetrics:
    """Runtime metrics for an instance."""

    timestamp: float = field(default_factory=time.time)
    reachable: bool = True
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
    latency_alert: bool = False
    latency_threshold_ms: int | None = None
    network_rtt_avg_ms: float | None = None
    network_rtt_recent_ms: float | None = None
    network_rtt_samples: int = 0

    def model_dump(self) -> JsonObject:
        """Convert to dictionary (compatibility method)."""
        return {
            "timestamp": self.timestamp,
            "reachable": self.reachable,
            "load_avg_1": self.load_avg_1,
            "load_avg_5": self.load_avg_5,
            "load_avg_15": self.load_avg_15,
            "cpu_usage_pct": self.cpu_usage_pct,
            "cpu_model": self.cpu_model,
            "cpu_count": self.cpu_count,
            "cpu_usage_pct_per_core": list(self.cpu_usage_pct_per_core) if self.cpu_usage_pct_per_core else None,
            "mem_total_mb": self.mem_total_mb,
            "mem_free_mb": self.mem_free_mb,
            "mem_used_mb": self.mem_used_mb,
            "mem_used_pct": self.mem_used_pct,
            "in_use_slots": self.in_use_slots,
            "in_use_engines": self.in_use_engines,
            "engine_processes": self.engine_processes,
            "latency_avg_ms": self.latency_avg_ms,
            "latency_recent_ms": self.latency_recent_ms,
            "latency_samples": self.latency_samples,
            "latency_alert": self.latency_alert,
            "latency_threshold_ms": self.latency_threshold_ms,
            "network_rtt_avg_ms": self.network_rtt_avg_ms,
            "network_rtt_recent_ms": self.network_rtt_recent_ms,
            "network_rtt_samples": self.network_rtt_samples,
        }


@dataclass
class InstanceActiveGameSide:
    """Represents a single side of an active game running on an instance."""

    role: Literal["black", "white"]
    engine_name: str
    pool_key: str

    def to_dict(self) -> JsonObject:
        return {
            "role": self.role,
            "engine_name": self.engine_name,
            "pool_key": self.pool_key,
        }


@dataclass
class InstanceActiveGame:
    """Metadata about a game currently using slots on an instance."""

    game_id: str
    black_engine: str
    white_engine: str
    initial_sfen: str
    roles: list[InstanceActiveGameSide] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    round_index: int | None = None
    time_control_black: str | None = None
    time_control_white: str | None = None

    def add_role(self, role: InstanceActiveGameSide) -> None:
        """Add or update a role assignment for this game."""

        for idx, existing in enumerate(self.roles):
            if existing.role == role.role and existing.pool_key == role.pool_key:
                self.roles[idx] = role
                break
        else:
            self.roles.append(role)

    def to_dict(self) -> JsonObject:
        return {
            "game_id": self.game_id,
            "black_engine": self.black_engine,
            "white_engine": self.white_engine,
            "initial_sfen": self.initial_sfen,
            "roles": [role.to_dict() for role in self.roles],
            "started_at": self.started_at,
            "round_index": self.round_index,
            "time_control_black": self.time_control_black,
            "time_control_white": self.time_control_white,
        }


@dataclass
class Instance:
    """Runtime instance with configuration and state."""

    config: InstanceConfig
    metrics: InstanceMetrics = field(default_factory=InstanceMetrics)
    drain: bool = False
    last_seen: float = field(default_factory=time.time)
    active_games: dict[str, InstanceActiveGame] = field(default_factory=dict)
    source_path: Path | None = None
    _network_rtt_samples: deque[float] = field(default_factory=lambda: deque(maxlen=120), repr=False)
    _network_rtt_sum: float = field(default=0.0, repr=False)
    _network_probe_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.is_local and self.metrics.cpu_count is None:
            try:
                count = os.cpu_count()
            except (AttributeError, OSError):
                count = None
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
        if self.drain:
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
        if self.drain:
            return 0
        limit = self.max_engine_capacity
        return max(0, limit - self.metrics.in_use_engines)

    @property
    def slot_capacity_known(self) -> bool:
        return self._slot_limit() is not None

    @property
    def engine_capacity_known(self) -> bool:
        if self.config.max_engines is not None and self.config.max_engines > 0:
            return True
        return self._slot_limit() is not None

    @property
    def can_accept_job(self) -> bool:
        """Whether instance can accept new jobs."""
        if not self.metrics.reachable or self.drain:
            return False
        if not self.slot_capacity_known:
            return False
        slot_limit = self._slot_limit()
        if slot_limit is not None and self.metrics.in_use_slots >= slot_limit:
            return False
        engine_limit = self.max_engine_capacity
        if self.metrics.in_use_engines >= engine_limit:
            return False
        return True

    def acquire_resources(self, *, slots: int = 0, engines: int = 0) -> bool:
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
        if not self.metrics.reachable or self.drain:
            return False

        if not self.slot_capacity_known:
            return False
        slot_limit = self._slot_limit()
        if slots and slot_limit is not None and (self.metrics.in_use_slots + slots) > slot_limit:
            return False

        if engines and not self.engine_capacity_known:
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

    def acquire_slot(self) -> bool:
        """Backward compatible single-slot acquisition helper."""
        return self.acquire_resources(slots=1)

    def release_slot(self) -> None:
        """Backward compatible single-slot release helper."""
        self.release_resources(slots=1)

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

        if new_metrics.reachable:
            self.last_seen = time.time()

    def to_dict(self) -> JsonObject:
        """Convert to dictionary for API responses."""
        slot_limit = self._slot_limit()
        engine_capacity = self.max_engine_capacity
        available_slots = None if slot_limit is None else max(0, slot_limit - self.metrics.in_use_slots)
        engine_limit = None if not self.engine_capacity_known else engine_capacity
        available_engines = None if engine_limit is None else max(0, engine_capacity - self.metrics.in_use_engines)
        return {
            "id": self.name,
            "type": self.type.value,
            "config": self.config.model_dump(),
            "metrics": self.metrics.model_dump(),
            "drain": self.drain,
            "last_seen": self.last_seen,
            "available_slots": available_slots,
            "can_accept_job": self.can_accept_job,
            "active_games": [game.to_dict() for game in self.active_games.values()],
            "config_path": str(self.source_path) if self.source_path else None,
            "is_local": self.is_local,
            "is_ssh": self.is_ssh,
            "engine_limit": engine_limit,
            "available_engines": available_engines,
            "slot_capacity": slot_limit,
        }

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
            except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
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
                except (RuntimeError, TypeError):  # pragma: no cover - defensive guard
                    return True
            elif value:
                return True
        return False


@dataclass
class InstancesConfig:
    """Configuration file format for instances."""

    instances: list[InstanceConfig]

    def __post_init__(self) -> None:
        """Validate that all instance IDs are unique."""
        names = [instance.name for instance in self.instances]
        if len(names) != len(set(names)):
            duplicates = [n for n in set(names) if names.count(n) > 1]
            raise ValueError(f"Duplicate instance names found: {duplicates}")

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "InstancesConfig":
        """Create instance from dict (compatibility method)."""
        instances_data = data.get("instances", [])
        instances: list[InstanceConfig] = []
        for inst_data in instances_data:
            # New spec: For SSH, take optional project_root (defaults to ~/ShogiArena-remote) and derive engine_dir.
            # engine_dir in YAML is deprecated and must not be provided.
            _type = InstanceType(inst_data["type"]) if "type" in inst_data else InstanceType.LOCAL
            _eng_spec = inst_data.get("engine_dir")
            if _type == InstanceType.LOCAL:
                if _eng_spec:
                    raise ValueError("engine_dir must not be specified for type=local")
                _eng = ""
                _proj = ""
            else:
                if _eng_spec:
                    raise ValueError("engine_dir must not be specified for type=ssh; use project_root instead or omit")
                project_root_raw = inst_data.get("project_root")
                if project_root_raw:
                    _proj = str(project_root_raw)
                else:
                    _proj = "$HOME/ShogiArena-remote"
                # Derive engine_dir from project_root
                _eng = str(Path(_proj) / "data" / "engines")

            raw_max_engines = inst_data.get("max_engines")
            raw_slots = inst_data.get("slots")
            slots = None if raw_slots in (None, "") else int(raw_slots)
            instance = InstanceConfig(
                name=str(inst_data["name"]).strip(),
                type=_type,
                engine_dir=_eng,
                project_root=_proj,
                host=inst_data.get("host"),
                user=inst_data.get("user"),
                port=inst_data.get("port", 22),
                identity_file=inst_data.get("identity_file"),
                slots=slots,
                max_engines=None if raw_max_engines in (None, "") else int(raw_max_engines),
                tags=inst_data.get("tags", []),
                strict_host_key_checking=bool(inst_data.get("strict_host_key_checking", True)),
                install_requirements=bool(inst_data.get("install_requirements", False)),
            )
            instances.append(instance)
        return cls(instances=instances)
