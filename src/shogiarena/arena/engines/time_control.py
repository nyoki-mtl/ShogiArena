"""
Time Control implementation for arena async system.

This module provides flexible time control management including fixed time,
time + increment, depth/node limits with safety margins, similar to cutechess/fastshogi.
"""

from __future__ import annotations

import logging
import time

import rshogi
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TimeControlSpecParseError(ValueError):
    """Raised when parsing a time-control spec string fails."""


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
        allow_timeout: If True, do not declare loss on time (soft TC)
        max_wait_ms: Max wait time (ms) per move when waiting for bestmove.
                     Useful when allow_timeout=True to avoid indefinite hangs.
    """

    time_ms: int | None = None
    increment_ms: int | None = None
    byoyomi_ms: int | None = None
    fixed_time_ms: int | None = None
    depth_limit: int | None = None
    node_limit: int | None = None
    expiry_margin_ms: int = 500
    allow_timeout: bool = False
    max_wait_ms: int = 600_000  # 10 minutes default upper bound

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
            if (self.byoyomi_ms or 0) > 0:
                base += f"+b{int(self.byoyomi_ms or 0)}"
            else:
                base += f"+i{int(self.increment_ms or 0)}"
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
        if self.allow_timeout:
            suffix.append("at")
        if self.expiry_margin_ms is not None:
            suffix.append(f"m{int(self.expiry_margin_ms)}")
        if self.max_wait_ms is not None:
            suffix.append(f"wt{int(self.max_wait_ms)}")
        return base if not suffix else base + ";" + ";".join(suffix)

    @classmethod
    def from_spec_str(cls, s: str) -> TimeControlLimits:
        """Decode compact spec string into TimeControlLimits.

        Strict parser for the format produced by ``to_spec_str()``.

        Grammar (simplified):
            spec   := base (';' suffix)*
            base   := 'fx' ms | 't' ms ['+' ('b' ms | 'i' ms)] | 's'
            suffix := 'd' depth | 'n' nodes | 'm' ms | 'at' | 'wt' ms

        All numeric fields are base-10 integers in milliseconds, except
        depth/nodes which are unit-less counts.
        Raises TimeControlSpecParseError for invalid inputs.
        """
        raw = (s or "").strip()
        if not raw:
            raise TimeControlSpecParseError("Empty time-control spec")

        def parse_uint(name: str, val: str) -> int:
            if not val or not val.isdigit():
                raise TimeControlSpecParseError(f"Invalid {name}: '{val}'")
            return int(val)

        base, *rest = raw.split(";")
        time_ms: int | None = None
        inc_ms: int | None = None
        byo_ms: int | None = None
        fixed_ms: int | None = None
        depth: int | None = None
        nodes: int | None = None
        margin: int = 500
        allow = False
        max_wait: int = 600_000

        # Base part
        if base.startswith("fx"):
            ms = parse_uint("fixed_ms", base[2:])
            if ms <= 0:
                raise TimeControlSpecParseError("fixed_ms must be > 0")
            fixed_ms = ms
        elif base.startswith("t"):
            # time in ms, optional +b{ms} or +i{ms}
            body = base[1:]
            if "+" in body:
                init_part, tail = body.split("+", 1)
                time_ms = parse_uint("time_ms", init_part)
                if tail.startswith("b"):
                    byo_ms = parse_uint("byoyomi_ms", tail[1:])
                elif tail.startswith("i"):
                    inc_ms = parse_uint("increment_ms", tail[1:])
                else:
                    raise TimeControlSpecParseError(f"Invalid base tail: '{tail}'")
            else:
                time_ms = parse_uint("time_ms", body)
        elif base == "s":
            # search-only, time not specified
            pass
        else:
            raise TimeControlSpecParseError(f"Invalid base: '{base}'")

        # Suffix parts
        for part in rest:
            p = part.strip()
            if not p:
                continue
            if p.startswith("d"):
                depth = parse_uint("depth_limit", p[1:])
            elif p.startswith("n"):
                nodes = parse_uint("node_limit", p[1:])
            elif p.startswith("m"):
                margin = parse_uint("expiry_margin_ms", p[1:])
            elif p == "at":
                allow = True
            elif p.startswith("wt"):
                max_wait = parse_uint("max_wait_ms", p[2:])
            else:
                raise TimeControlSpecParseError(f"Unknown suffix: '{p}'")

        return cls(
            time_ms=time_ms,
            increment_ms=inc_ms,
            byoyomi_ms=byo_ms,
            fixed_time_ms=fixed_ms,
            depth_limit=depth,
            node_limit=nodes,
            expiry_margin_ms=margin,
            allow_timeout=allow,
            max_wait_ms=max_wait,
        )


def limits_to_record_time_spec(limits: TimeControlLimits) -> str:
    """Convert arena limits (ms units) into rshogi record time-control spec."""
    fixed_time_ms = limits.fixed_time_ms
    if fixed_time_ms:
        base_seconds = max(int(fixed_time_ms) // 1000, 0)
        return rshogi.record.TimeControl(base_seconds, 0, 0).to_spec()

    base_seconds = max(int(limits.time_ms or 0) // 1000, 0)
    byoyomi_seconds = max(int(limits.byoyomi_ms or 0) // 1000, 0)
    increment_seconds = max(int(limits.increment_ms or 0) // 1000, 0)
    return rshogi.record.TimeControl(base_seconds, byoyomi_seconds, increment_seconds).to_spec()


class TimeControl:
    """
    Time control management for USI engines.

    Handles various time control modes:
    - Fixed time per move
    - Remaining time + increment
    - Depth/node limits
    - Expiry detection with safety margins
    """

    def __init__(self, limits: TimeControlLimits) -> None:
        """
        Initialize time control with given limits.

        Args:
            limits: Time control configuration
        """
        self.limits = limits
        self.remaining_time_ms = limits.time_ms or 0
        self.move_count = 0
        self.last_move_start_time: float | None = None
        self.last_move_duration_ms = 0
        self._expired = False

        # Validate incompatible combinations
        fixed = limits.fixed_time_ms is not None
        has_time = limits.time_ms is not None
        has_inc = (limits.increment_ms or 0) > 0
        has_byo = (limits.byoyomi_ms or 0) > 0

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
                "Invalid TimeControl configuration: fixed_time_ms cannot be combined with "
                "time_ms/increment_ms/byoyomi_ms"
            )
            logger.error(msg)
            raise ValueError(msg)

        if has_inc and has_byo:
            msg = "Invalid TimeControl configuration: increment_ms and byoyomi_ms cannot be used together"
            logger.error(msg)
            raise ValueError(msg)

        if (has_inc or has_byo) and not has_time:
            msg = "Invalid TimeControl configuration: time_ms must be set when using increment_ms or byoyomi_ms"
            logger.error(msg)
            raise ValueError(msg)

        # Validate configuration
        if limits.fixed_time_ms:
            self.mode = "fixed"
            logger.debug(f"TimeControl initialized in fixed mode: {limits.fixed_time_ms}ms per move")
        elif limits.time_ms is not None and (limits.byoyomi_ms or 0) > 0:
            self.mode = "time_byoyomi"
            logger.debug(
                f"TimeControl initialized in time+byoyomi mode: {limits.time_ms}ms + b{limits.byoyomi_ms or 0}ms"
            )
        elif limits.time_ms is not None:
            self.mode = "time_increment"
            logger.debug(
                f"TimeControl initialized in time+increment mode: {limits.time_ms}ms + {limits.increment_ms or 0}ms"
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
        self.remaining_time_ms = self.limits.time_ms or 0
        self.move_count = 0
        self.last_move_start_time = None
        self.last_move_duration_ms = 0
        self._expired = False
        logger.debug(f"TimeControl initialized for game: remaining={self.remaining_time_ms}ms")

    def start_timer(self) -> None:
        """Start timing for current move."""
        self.last_move_start_time = time.perf_counter()
        logger.debug(f"Timer started for move {self.move_count + 1}")

    def expired(self) -> bool:
        """Check if time has expired."""
        return self._expired

    def last_move_time_ms(self) -> int:
        """Get duration of last move in milliseconds."""
        return self.last_move_duration_ms

    def active_time_left_ms(self) -> int:
        """Get active time left in milliseconds."""
        if self.mode == "fixed":
            return self.limits.fixed_time_ms or 0
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
                total = int(self.remaining_time_ms + (self.limits.byoyomi_ms or 0))
                base_ms = int(total + self.limits.expiry_margin_ms + 1000)
                min_ms = 1000
                max_ms = int(total + 3000)
                return max(min_ms, min(base_ms, max_ms))
            # For search limits, use a generous default (10 minutes)
            return 600_000

        base_ms = base_timeout_ms()
        if getattr(self.limits, "allow_timeout", False):
            # Soft overtime: allow exceeding nominal budget but never wait forever.
            cap = int(self.limits.max_wait_ms or 600_000)
            return min(base_ms, cap) / 1000.0
        return base_ms / 1000.0

    def __str__(self) -> str:
        """String representation for debugging."""
        if self.mode == "fixed":
            return f"TimeControl(fixed={self.limits.fixed_time_ms}ms)"
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
            return f"TimeControl(depth={self.limits.depth_limit}, nodes={self.limits.node_limit})"

    def update_after_move(self, apply_increment: bool = True) -> None:
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
            if apply_increment and self.limits.increment_ms:
                self.remaining_time_ms += self.limits.increment_ms
            # Expire only if move duration strictly exceeds (prev + margin)
            allowed_ms = max(0, prev_remaining)
            if not self.limits.allow_timeout and self.last_move_duration_ms > allowed_ms + self.limits.expiry_margin_ms:
                self._expired = True
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
            allowed_ms = max(0, prev_remaining) + (self.limits.byoyomi_ms or 0)
            if self.last_move_duration_ms > allowed_ms + self.limits.expiry_margin_ms:
                self._expired = True
                logger.warning(
                    f"Time expired (byoyomi): duration={self.last_move_duration_ms}ms, allowed={allowed_ms}ms, "
                    f"margin={self.limits.expiry_margin_ms}ms"
                )

        else:
            # fixed/search_limits keep previous behavior for accounting only
            pass

        self.move_count += 1
        self.last_move_start_time = None
        logger.debug(
            f"Move {self.move_count} completed: duration={self.last_move_duration_ms}ms, "
            f"remaining={self.remaining_time_ms}ms"
        )
