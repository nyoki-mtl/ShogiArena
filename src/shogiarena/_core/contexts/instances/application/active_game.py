"""Active game tracking models for instances."""

import time
from dataclasses import dataclass, field
from typing import Literal

from shogiarena._core.shared.kernel.json_types import JsonObject


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
    started_ts_sec: float = field(default_factory=time.time)
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
            "started_at": self.started_ts_sec,
            "round_index": self.round_index,
            "time_control_black": self.time_control_black,
            "time_control_white": self.time_control_white,
        }


__all__ = ["InstanceActiveGame", "InstanceActiveGameSide"]
