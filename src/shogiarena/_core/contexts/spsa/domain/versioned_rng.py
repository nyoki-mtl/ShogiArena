"""Versioned deterministic random derivation for SPSA assignments."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Final, Literal, Protocol

SPSA_RNG_SCHEMA: Final = "shogiarena.spsa.hmac-sha256.v1"
SpsaRngDomain = Literal[
    "spsa.flip",
    "spsa.opening",
    "spsa.rounding",
    "spsa.retry",
    "spsa.node_multiplier",
]


class RandomSource(Protocol):
    """Small random-source surface consumed by SPSA arithmetic."""

    def random(self) -> float: ...

    def randrange(self, stop: int) -> int: ...

    def randint(self, start: int, stop: int) -> int: ...


@dataclass
class HmacSha256Rng:
    """HMAC-SHA256 counter stream scoped to one canonical SPSA identity."""

    seed_hex: str
    domain: SpsaRngDomain
    run_id: str
    update_idx: int
    pair_idx: int | None = None
    parameter_id: str | None = None
    counter: int = 0

    def __post_init__(self) -> None:
        if len(self.seed_hex) != 64:
            raise ValueError("SPSA run seed must be exactly 256 bits encoded as 64 hexadecimal characters")
        try:
            bytes.fromhex(self.seed_hex)
        except ValueError as exc:
            raise ValueError("SPSA run seed must be hexadecimal") from exc
        if self.counter < 0:
            raise ValueError("SPSA RNG counter must be non-negative")

    def next_block(self) -> bytes:
        """Return the next 256-bit block in the scoped counter stream."""

        context = {
            "counter": self.counter,
            "domain": self.domain,
            "pair_idx": self.pair_idx,
            "parameter_id": self.parameter_id,
            "run_id": self.run_id,
            "schema": SPSA_RNG_SCHEMA,
            "update_idx": self.update_idx,
        }
        message = json.dumps(context, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        self.counter += 1
        return hmac.new(bytes.fromhex(self.seed_hex), message, hashlib.sha256).digest()

    def random(self) -> float:
        """Return a deterministic value in the half-open interval [0, 1)."""

        return int.from_bytes(self.next_block(), "big") / (1 << 256)

    def randbelow(self, upper_bound: int) -> int:
        """Return an unbiased deterministic integer in ``range(upper_bound)``."""

        if upper_bound <= 0:
            raise ValueError("upper_bound must be positive")
        limit = (1 << 256) - ((1 << 256) % upper_bound)
        while True:
            candidate = int.from_bytes(self.next_block(), "big")
            if candidate < limit:
                return candidate % upper_bound

    def randrange(self, stop: int) -> int:
        """Return an integer in ``range(stop)``."""

        return self.randbelow(stop)

    def randint(self, start: int, stop: int) -> int:
        """Return an integer in the inclusive interval ``[start, stop]``."""

        if stop < start:
            raise ValueError("stop must be greater than or equal to start")
        return start + self.randbelow(stop - start + 1)
