"""Shared time-control limits model and runtime clock."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from shogiarena._core.shared.kernel.time_control_spec import limits_to_record_time_spec

logger = logging.getLogger(__name__)

# Default upper bound (ms) on how long to wait for a bestmove. Kept as a module
# constant so callers can reference the default without touching a Pydantic model
# class attribute (model fields are not exposed on the class in Pydantic v2).
DEFAULT_MAX_WAIT_MS = 600_000  # 10 minutes

_TIME_CONTROL_KEY_HINTS = {
    "byoyomi": "byoyomi_ms",
    "depth": "depth_limit",
    "fixed_time": "fixed_time_ms",
    "increment": "increment_ms",
    "nodes": "node_limit",
}


@runtime_checkable
class TimeControlLimitsPort(Protocol):
    """Required shape for time-control values consumed across layers."""

    time_ms: int | None
    increment_ms: int | None
    byoyomi_ms: int | None
    fixed_time_ms: int | None
    depth_limit: int | None
    node_limit: int | None
    expiry_margin_ms: int
    should_allow_timeout: bool
    max_wait_ms: int


class TimeControlLimits(BaseModel):
    """
    Time control configuration limits.

    Args:
        time_ms: Base time in milliseconds per side
        increment_ms: Time increment per move in milliseconds (Fischer)
        byoyomi_ms: Byoyomi per move in milliseconds (USI byoyomi)
        fixed_time_ms: Fixed time per move in milliseconds (overrides time/increment/byoyomi)
        depth_limit: Maximum depth limit
        node_limit: Maximum node search limit
        expiry_margin_ms: Safety margin before expiry in milliseconds
        should_allow_timeout: If True, do not declare loss on time (soft TC)
        max_wait_ms: Max wait time (ms) per move when waiting for bestmove.
                     Useful when should_allow_timeout=True to avoid indefinite hangs.
    """

    model_config = ConfigDict(extra="forbid")

    time_ms: int | None = None
    increment_ms: int | None = None
    byoyomi_ms: int | None = None
    fixed_time_ms: int | None = None
    depth_limit: int | None = None
    node_limit: int | None = None
    expiry_margin_ms: int = 500
    should_allow_timeout: bool = False
    max_wait_ms: int = DEFAULT_MAX_WAIT_MS  # 10 minutes default upper bound

    @model_validator(mode="before")
    @classmethod
    def _reject_known_alias_keys(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        for key, replacement in _TIME_CONTROL_KEY_HINTS.items():
            if key in value:
                raise ValueError(f"time_control.{key} is not a valid time_control key; use {replacement}")
        return value

    # --- Encoding helpers (DB/UI spec string) ----------------------------
    def to_spec_str(self) -> str:
        """Encode limits into compact spec string for DB/UI with ms precision.

        Grammar (base):
            - Fixed:      "fx{ms}"
            - Time only:  "t{ms}"
            - Time+inc:   "t{ms}+i{ms}"
            - Time+byo:   "t{ms}+b{ms}"
            - Search-only:"s"  (when depth/nodes only)

        Suffixes (order: d, n, at, m, wt):
            - d{depth}  n{nodes}  at  m{margin_ms}  wt{max_wait_ms}
        """
        # Base part (preserve ms precision)
        if self.fixed_time_ms is not None and self.fixed_time_ms > 0:
            base = f"fx{int(self.fixed_time_ms)}"
        elif self.time_ms is not None:
            base = f"t{int(self.time_ms)}"
            if self.byoyomi_ms is not None and self.byoyomi_ms > 0:
                base += f"+b{int(self.byoyomi_ms)}"
            elif self.increment_ms is not None:
                base += f"+i{int(self.increment_ms)}"
        elif self.depth_limit is not None or self.node_limit is not None:
            base = "s"
        else:
            # Fallback shouldn't normally happen due to validation, keep explicit for safety
            base = "s"

        # Suffixes
        suffix: list[str] = []
        if self.depth_limit is not None:
            suffix.append(f"d{int(self.depth_limit)}")
        if self.node_limit is not None:
            suffix.append(f"n{int(self.node_limit)}")
        if self.should_allow_timeout:
            suffix.append("at")
        if self.expiry_margin_ms is not None:
            suffix.append(f"m{int(self.expiry_margin_ms)}")
        if self.max_wait_ms is not None:
            suffix.append(f"wt{int(self.max_wait_ms)}")
        return base if not suffix else base + ";" + ";".join(suffix)


def coerce_time_control_limits(raw: TimeControlLimitsPort) -> TimeControlLimits:
    """Normalize any port-compatible limits object into ``TimeControlLimits``."""

    if isinstance(raw, TimeControlLimits):
        return raw
    return TimeControlLimits(
        time_ms=raw.time_ms,
        increment_ms=raw.increment_ms,
        byoyomi_ms=raw.byoyomi_ms,
        fixed_time_ms=raw.fixed_time_ms,
        depth_limit=raw.depth_limit,
        node_limit=raw.node_limit,
        expiry_margin_ms=raw.expiry_margin_ms,
        should_allow_timeout=raw.should_allow_timeout,
        max_wait_ms=raw.max_wait_ms,
    )


class GameClock:
    """
    Per-side game clock that tracks remaining time during a match.

    Wraps :class:`TimeControlLimits` (the static rules) with mutable runtime
    state: remaining time, move counter, and expiry flag.

    Supported modes (determined by *limits*):
    - Fixed time per move
    - Remaining time + increment (Fischer)
    - Remaining time + byoyomi
    - Depth/node search limits
    """

    def __init__(self, limits: TimeControlLimits) -> None:
        """
        Initialize time control with given limits.

        Args:
            limits: Time control configuration
        """
        self.limits = limits
        self.remaining_time_ms = limits.time_ms if limits.time_ms is not None else 0
        self.move_count = 0
        self.last_move_start_time: float | None = None
        self.last_move_duration_ms = 0
        self._is_expired = False

        # Validate incompatible combinations
        fixed = limits.fixed_time_ms is not None
        has_time = limits.time_ms is not None
        has_inc = limits.increment_ms is not None and limits.increment_ms > 0
        has_byo = limits.byoyomi_ms is not None and limits.byoyomi_ms > 0

        # Basic numeric validation
        if limits.expiry_margin_ms is not None and limits.expiry_margin_ms < 0:
            raise ValueError("expiry_margin_ms must be >= 0")
        if limits.increment_ms is not None and limits.increment_ms < 0:
            raise ValueError("increment_ms must be >= 0")
        if limits.byoyomi_ms is not None and limits.byoyomi_ms < 0:
            raise ValueError("byoyomi_ms must be >= 0")
        if limits.fixed_time_ms is not None and limits.fixed_time_ms <= 0:
            raise ValueError("fixed_time_ms must be > 0 when specified")
        if limits.max_wait_ms is not None and limits.max_wait_ms <= 0:
            raise ValueError("max_wait_ms must be > 0")

        if fixed and (has_time or has_inc or has_byo):
            msg = (
                "Invalid GameClock configuration: fixed_time_ms cannot be combined with time_ms/increment_ms/byoyomi_ms"
            )
            logger.error(msg)
            raise ValueError(msg)

        if has_inc and has_byo:
            msg = "Invalid GameClock configuration: increment_ms and byoyomi_ms cannot be used together"
            logger.error(msg)
            raise ValueError(msg)

        if (has_inc or has_byo) and not has_time:
            msg = "Invalid GameClock configuration: time_ms must be set when using increment_ms or byoyomi_ms"
            logger.error(msg)
            raise ValueError(msg)

        # Validate configuration
        if limits.fixed_time_ms:
            self.mode = "fixed"
            logger.debug(f"GameClock initialized in fixed mode: {limits.fixed_time_ms}ms per move")
        elif limits.time_ms is not None and limits.byoyomi_ms is not None and limits.byoyomi_ms > 0:
            self.mode = "time_byoyomi"
            logger.debug(f"TimeControl initialized in time+byoyomi mode: {limits.time_ms}ms + b{limits.byoyomi_ms}ms")
        elif limits.time_ms is not None:
            self.mode = "time_increment"
            logger.debug(
                f"TimeControl initialized in time+increment mode: {limits.time_ms}ms + {limits.increment_ms}ms"
            )
        elif limits.depth_limit or limits.node_limit:
            self.mode = "search_limits"
            logger.debug(
                f"TimeControl initialized in search limits mode: depth={limits.depth_limit}, nodes={limits.node_limit}"
            )
        else:
            raise ValueError("TimeControlLimits must specify at least one limit type")

    def initialize_for_game(self) -> None:
        """Initialize time control for a new game."""
        self.remaining_time_ms = self.limits.time_ms if self.limits.time_ms is not None else 0
        self.move_count = 0
        self.last_move_start_time = None
        self.last_move_duration_ms = 0
        self._is_expired = False
        logger.debug(f"GameClock initialized for game: remaining={self.remaining_time_ms}ms")

    def start_timer(self) -> None:
        """Start timing for current move."""
        self.last_move_start_time = time.perf_counter()
        logger.debug(f"Timer started for move {self.move_count + 1}")

    def is_expired(self) -> bool:
        """Check if time has expired."""
        return self._is_expired

    def active_time_left_ms(self) -> int:
        """Get active time left in milliseconds."""
        if self.mode == "fixed":
            fixed_time_ms = self.limits.fixed_time_ms
            return fixed_time_ms if fixed_time_ms is not None else 0
        return max(0, self.remaining_time_ms)

    def get_timeout_for_wait(self) -> float | None:
        """
        Get timeout value for waiting bestmove, including safety margin.

        Returns:
            Timeout in seconds, or None for no limit
        """

        def base_timeout_ms() -> int:
            if self.mode == "fixed" and self.limits.fixed_time_ms:
                return int(self.limits.fixed_time_ms + self.limits.expiry_margin_ms + 1000)
            elif self.mode == "time_increment":
                # Exclude increment from bestmove wait timeout to cap usage to main time.
                base_ms = int(self.remaining_time_ms + self.limits.expiry_margin_ms + 1000)
                # Min 1 second, cap at remaining + 3s
                min_ms = 1000
                max_ms = int(self.remaining_time_ms + 3000)
                return max(min_ms, min(base_ms, max_ms))
            elif self.mode == "time_byoyomi":
                # byoyomi_ms is guaranteed non-None when mode == "time_byoyomi"
                # (validated in __init__ at mode assignment)
                byoyomi_ms = self.limits.byoyomi_ms
                assert byoyomi_ms is not None  # narrowing for type checkers
                total = int(self.remaining_time_ms + byoyomi_ms)
                base_ms = int(total + self.limits.expiry_margin_ms + 1000)
                min_ms = 1000
                max_ms = int(total + 3000)
                return max(min_ms, min(base_ms, max_ms))
            # For search limits, use a generous default (10 minutes)
            return 600_000

        base_ms = base_timeout_ms()
        if self.limits.should_allow_timeout:
            # Soft overtime: allow exceeding nominal budget but never wait forever.
            cap = int(self.limits.max_wait_ms)
            return min(base_ms, cap) / 1000.0
        return base_ms / 1000.0

    def __str__(self) -> str:
        """String representation for debugging."""
        if self.mode == "fixed":
            return f"GameClock(fixed={self.limits.fixed_time_ms}ms)"
        elif self.mode == "time_increment":
            return (
                f"TimeControl(time={self.remaining_time_ms}ms, inc={self.limits.increment_ms}ms, "
                f"moves={self.move_count})"
            )
        elif self.mode == "time_byoyomi":
            return (
                f"TimeControl(time={self.remaining_time_ms}ms, byo={self.limits.byoyomi_ms}ms, moves={self.move_count})"
            )
        else:
            return f"GameClock(depth={self.limits.depth_limit}, nodes={self.limits.node_limit})"

    def update_after_move(self, should_apply_increment: bool = True) -> None:
        # Override to extend with byoyomi handling while keeping original logic
        if self.last_move_start_time is None:
            logger.warning("update_after_move called without start_timer")
            return

        now = time.perf_counter()
        self.last_move_duration_ms = int((now - self.last_move_start_time) * 1000)

        if self.mode == "time_increment":
            prev_remaining = self.remaining_time_ms
            # Subtract spent time and then apply increment (Fischer applies after move)
            self.remaining_time_ms = max(0, self.remaining_time_ms - self.last_move_duration_ms)
            if should_apply_increment and self.limits.increment_ms:
                self.remaining_time_ms += self.limits.increment_ms
            # Expire only if move duration strictly exceeds (prev + margin)
            allowed_ms = max(0, prev_remaining)
            if (
                not self.limits.should_allow_timeout
                and self.last_move_duration_ms > allowed_ms + self.limits.expiry_margin_ms
            ):
                self._is_expired = True
                logger.warning(
                    f"Time expired (increment): duration={self.last_move_duration_ms}ms, prev={prev_remaining}ms, "
                    f"margin={self.limits.expiry_margin_ms}ms"
                )

        elif self.mode == "time_byoyomi":
            # Subtract move duration from remaining main time
            prev_remaining = self.remaining_time_ms
            self.remaining_time_ms -= self.last_move_duration_ms
            if self.remaining_time_ms < 0:
                self.remaining_time_ms = 0
            # Expire if exceeded main + byoyomi (with margin)
            # byoyomi_ms is guaranteed non-None when mode == "time_byoyomi"
            # (validated in __init__ at mode assignment)
            byoyomi_ms = self.limits.byoyomi_ms
            assert byoyomi_ms is not None  # narrowing for type checkers
            allowed_ms = max(0, prev_remaining) + byoyomi_ms
            if self.last_move_duration_ms > allowed_ms + self.limits.expiry_margin_ms:
                self._is_expired = True
                logger.warning(
                    f"Time expired (byoyomi): duration={self.last_move_duration_ms}ms, allowed={allowed_ms}ms, "
                    f"margin={self.limits.expiry_margin_ms}ms"
                )

        # fixed/search_limits keep previous behavior for accounting only

        self.move_count += 1
        self.last_move_start_time = None
        logger.debug(
            f"Move {self.move_count} completed: duration={self.last_move_duration_ms}ms, "
            f"remaining={self.remaining_time_ms}ms"
        )


__all__ = [
    "DEFAULT_MAX_WAIT_MS",
    "GameClock",
    "TimeControlLimits",
    "TimeControlLimitsPort",
    "coerce_time_control_limits",
    "limits_to_record_time_spec",
]
