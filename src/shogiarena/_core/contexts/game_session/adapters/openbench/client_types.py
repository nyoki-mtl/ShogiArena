"""Core OpenBench client data models and validation helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int

_OPENBENCH_SPRT_PAIR_PATTERN = re.compile(r"^\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]$")


class OpenBenchError(RuntimeError):
    pass


def parse_openbench_float_pair(value: str, *, field_name: str) -> tuple[float, float]:
    match = _OPENBENCH_SPRT_PAIR_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be formatted as [x, y]")
    left = coerce_float(match.group(1))
    right = coerce_float(match.group(2))
    if left is None or right is None:  # pragma: no cover - regex keeps this defensive
        raise ValueError(f"{field_name} must contain numeric values")
    return left, right


@dataclass(slots=True)
class OpenBenchCounters:
    losses: int = 0
    draws: int = 0
    wins: int = 0
    ll: int = 0
    ld: int = 0
    dd: int = 0
    dw: int = 0
    ww: int = 0
    crashes: int = 0
    timelosses: int = 0
    illegals: int = 0

    @property
    def games(self) -> int:
        return self.losses + self.draws + self.wins

    def to_payload(self) -> dict[str, int | str]:
        return {
            "trinomial": f"{self.losses} {self.draws} {self.wins}",
            "pentanomial": f"{self.ll} {self.ld} {self.dd} {self.dw} {self.ww}",
            "crashes": self.crashes,
            "timelosses": self.timelosses,
            "illegals": self.illegals,
        }

    def to_state(self) -> dict[str, int]:
        return {
            "losses": self.losses,
            "draws": self.draws,
            "wins": self.wins,
            "ll": self.ll,
            "ld": self.ld,
            "dd": self.dd,
            "dw": self.dw,
            "ww": self.ww,
            "crashes": self.crashes,
            "timelosses": self.timelosses,
            "illegals": self.illegals,
        }

    @classmethod
    def from_state(cls, data: Mapping[str, JsonValue]) -> OpenBenchCounters:
        return cls(
            losses=coerce_int(data.get("losses")) or 0,
            draws=coerce_int(data.get("draws")) or 0,
            wins=coerce_int(data.get("wins")) or 0,
            ll=coerce_int(data.get("ll")) or 0,
            ld=coerce_int(data.get("ld")) or 0,
            dd=coerce_int(data.get("dd")) or 0,
            dw=coerce_int(data.get("dw")) or 0,
            ww=coerce_int(data.get("ww")) or 0,
            crashes=coerce_int(data.get("crashes")) or 0,
            timelosses=coerce_int(data.get("timelosses")) or 0,
            illegals=coerce_int(data.get("illegals")) or 0,
        )

    def delta_from(self, base: OpenBenchCounters) -> OpenBenchCounters:
        return OpenBenchCounters(
            losses=max(0, self.losses - base.losses),
            draws=max(0, self.draws - base.draws),
            wins=max(0, self.wins - base.wins),
            ll=max(0, self.ll - base.ll),
            ld=max(0, self.ld - base.ld),
            dd=max(0, self.dd - base.dd),
            dw=max(0, self.dw - base.dw),
            ww=max(0, self.ww - base.ww),
            crashes=max(0, self.crashes - base.crashes),
            timelosses=max(0, self.timelosses - base.timelosses),
            illegals=max(0, self.illegals - base.illegals),
        )

    def is_empty(self) -> bool:
        return (
            self.games == 0
            and self.ll == 0
            and self.ld == 0
            and self.dd == 0
            and self.dw == 0
            and self.ww == 0
            and self.crashes == 0
            and self.timelosses == 0
            and self.illegals == 0
        )


@dataclass(slots=True)
class OpenBenchClientConfig:
    is_enabled: bool
    mode: str
    server: str
    username: str
    password: str
    target_test_id: int | None
    submit_interval_games: int
    is_strict: bool
    heartbeat_interval_sec: float
    poll_interval_sec: float
    assignment_timeout_sec: float
    is_insecure_http_allowed: bool
    concurrency: int
    create_payload: dict[str, str] | None = None
    create_discovery_timeout_sec: float = 180.0

    def validate(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("openbench.server must start with http:// or https://")
        if parsed.scheme != "https" and not self.is_insecure_http_allowed:
            raise ValueError("openbench.server must use https:// unless allow_insecure_http=true")
        if not parsed.netloc:
            raise ValueError("openbench.server must include host")
        if self.submit_interval_games <= 0:
            raise ValueError("openbench.submit_interval_games must be > 0")
        if self.heartbeat_interval_sec <= 0:
            raise ValueError("openbench.heartbeat_interval_sec must be > 0")
        if self.mode not in {"existing_test", "create_test"}:
            raise ValueError("openbench.mode must be 'existing_test' or 'create_test'")
        if self.mode == "existing_test":
            if self.target_test_id is None or self.target_test_id <= 0:
                raise ValueError("openbench.target_test_id must be > 0 in existing_test mode")
        if self.mode == "create_test" and not self.create_payload:
            raise ValueError("openbench.create payload is required in create_test mode")


__all__ = [
    "parse_openbench_float_pair",
    "OpenBenchClientConfig",
    "OpenBenchCounters",
    "OpenBenchError",
]
