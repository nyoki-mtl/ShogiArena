"""Ports for the transactional SPSA ledger runtime。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from shogiarena._core.contexts.spsa.domain.ledger_models import (
    LedgerGameObservation,
    LedgerPairAssignment,
    SpsaUpdateState,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.database_types import DatabaseServicePort
from shogiarena._core.shared.kernel.json_types import JsonObject

SPSA_LEDGER_RELATIVE_PATH = Path("spsa") / "ledger.sqlite3"


class SpsaLedgerHandlePort(Protocol):
    """Closable run-scoped ledger connection handle。"""

    def close(self) -> None: ...


class SpsaObservationLedgerPort(Protocol):
    """Observation reconciliation operations used by the runner。"""

    def validate_reconciliation(self, *, run_id: str, source: DatabaseServicePort) -> int: ...

    def reconcile(self, *, run_id: str, source: DatabaseServicePort) -> int: ...

    def insert_committed(self, observation: LedgerGameObservation) -> bool: ...


class SpsaLedgerRuntimePort(Protocol):
    """Update transaction operations used by SPSA orchestration。"""

    def initialize_run(
        self,
        *,
        resume_hash: str,
        space_digest: str,
        sealed_run_seed: str,
        contract: JsonObject,
        params: list[ParamEntry],
    ) -> None: ...

    def plan_update(
        self,
        *,
        update_idx: int,
        theta_before: Mapping[str, float],
        schedule: JsonObject,
        ltc_required: bool,
    ) -> None: ...

    def assign_pair(
        self,
        *,
        update_idx: int,
        pair_id: str,
        assignment_kind: str,
        opening: JsonObject,
        color_assignment: JsonObject,
        flips: Mapping[str, int],
        rounding_samples: JsonObject,
    ) -> None: ...

    def transition(self, *, update_idx: int, target: SpsaUpdateState) -> None: ...

    def mark_games_complete(self, *, update_idx: int) -> None: ...

    def mark_games_running(self, *, update_idx: int) -> None: ...

    def record_variant_quarantine(
        self,
        *,
        update_idx: int,
        pair_id: str,
        variant_id: str,
        failure_classification: str,
    ) -> None: ...

    def store_candidate(
        self,
        *,
        update_idx: int,
        theta_candidate: Mapping[str, float],
        schedule: JsonObject,
    ) -> None: ...

    def commit_without_ltc(
        self,
        *,
        update_idx: int,
        theta_final: Mapping[str, float],
    ) -> None: ...

    def start_ltc(self, *, update_idx: int) -> None: ...

    def assign_ltc_pairs_and_start(
        self,
        *,
        update_idx: int,
        assignments: Sequence[LedgerPairAssignment],
    ) -> None: ...

    def prepare_ltc(self, *, update_idx: int) -> None: ...

    def commit_ltc_decision(
        self,
        *,
        update_idx: int,
        baseline_update_idx: int,
        is_passed: bool,
        evidence: JsonObject,
        accepted_theta: Mapping[str, float],
        reverted_theta: Mapping[str, float],
    ) -> None: ...

    def game_result_kind(self, *, game_id: str) -> str | None: ...

    def accepted_baseline(self) -> tuple[int, dict[str, float]]: ...

    def accepted_best_commit(self, *, update_idx: int) -> JsonObject: ...

    def latest_accepted_update_idx(self) -> int | None: ...

    def completed_updates(self) -> int: ...

    def current_theta(self) -> dict[str, float]: ...

    def assert_resume_allowed(self) -> bool: ...

    def resume_disposition(self, *, total_updates: int) -> str: ...

    def invalidate_resumable_terminal(self) -> None: ...

    def commit_terminal(self, *, status: str, reason: str, resumable: bool) -> None: ...

    def terminal_payload(self) -> JsonObject | None: ...

    def completion_status_payload(self, *, cleanup_error: str | None = None) -> JsonObject | None: ...

    def project_derived_json(self, *, run_dir: Path) -> None: ...


__all__ = [
    "SPSA_LEDGER_RELATIVE_PATH",
    "SpsaLedgerHandlePort",
    "SpsaLedgerRuntimePort",
    "SpsaObservationLedgerPort",
]
