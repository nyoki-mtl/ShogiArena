"""Result model definitions for persisted run outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import RulesConfig
from shogiarena._core.contexts.game_session.application.sprt_service import SprtResult
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize


@dataclass(slots=True, kw_only=True)
class _RunResultBase:
    """Runストレージと紐づく長期保持可能な結果メタ情報。"""

    run_id: str
    run_dir: Path
    storage: RunStoragePort
    summary: Mapping[str, JsonValue] | None = None
    config_snapshot: Mapping[str, JsonValue] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def duration_ms(self) -> int | None:
        if self.started_at is None or self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return int(delta.total_seconds() * 1000)

    def serialize_extras(self) -> JsonObject:
        return {}


@dataclass(slots=True, kw_only=True)
class SpsaRunResult(_RunResultBase):
    """SPSAランナー用の結果構造。"""

    final_params: Mapping[str, float] = field(default_factory=dict)

    def serialize_extras(self) -> JsonObject:
        return {"final_params": json_serialize(self.final_params)}


@dataclass(slots=True, kw_only=True)
class TournamentRunResult(_RunResultBase):
    """トーナメントランナー用の結果構造。"""

    tournament: TournamentResults
    sprt: SprtResult | None = None

    def serialize_extras(self) -> JsonObject:
        return {
            "tournament": json_serialize(self.tournament),
            "sprt": json_serialize(self.sprt),
        }


@dataclass(slots=True, kw_only=True)
class TournamentRunResultBuilder:
    """Build TournamentRunResult from finalized tournament/SPRT outputs."""

    run_id: str
    run_dir: Path
    storage: RunStoragePort

    def build_tournament_run_result(
        self,
        results: TournamentResults,
        sprt_status: SprtResult | None,
    ) -> TournamentRunResult:
        return TournamentRunResult(
            tournament=results,
            sprt=sprt_status,
            run_id=self.run_id,
            run_dir=self.run_dir,
            storage=self.storage,
        )


def serialize_rules_config(rules: RulesConfig | None) -> JsonObject:
    if rules is None:
        return {}

    payload: JsonObject = rules.model_dump(mode="json")

    if rules.time_control is not None:
        payload["time_control_spec"] = rules.time_control.to_spec_str()
    return payload


__all__ = [
    "SpsaRunResult",
    "TournamentRunResult",
    "TournamentRunResultBuilder",
    "serialize_rules_config",
]
