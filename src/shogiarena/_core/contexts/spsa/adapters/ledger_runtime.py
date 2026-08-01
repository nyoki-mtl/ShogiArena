"""Transactional SPSA update ledger service。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from shogiarena._core.contexts.game_session.application.sprt_service import (
    PENTANOMIAL_MIN_PAIRS_FOR_LLR,
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    Sprt,
    SprtDecision,
    SprtResult,
)
from shogiarena._core.contexts.spsa.adapters.ledger_projection import project_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.ledger_schema import (
    CURRENT_SPSA_LEDGER_SCHEMA_VERSION,
    schema_digest,
)
from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression_events import (
    determine_ltc_status,
    fail_closed_ltc_status_at_budget,
)
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerPairAssignment, SpsaUpdateState
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.domain.versioned_rng import SPSA_RNG_SCHEMA
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.hash_normalization import HashInput
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_json_bytes, canonical_sha256

_ALLOWED_TRANSITIONS: dict[SpsaUpdateState, frozenset[SpsaUpdateState]] = {
    SpsaUpdateState.PLANNED: frozenset({SpsaUpdateState.GAMES_RUNNING}),
    SpsaUpdateState.GAMES_RUNNING: frozenset({SpsaUpdateState.GAMES_COMPLETE}),
    SpsaUpdateState.GAMES_COMPLETE: frozenset({SpsaUpdateState.CANDIDATE_COMPUTED}),
    SpsaUpdateState.CANDIDATE_COMPUTED: frozenset({SpsaUpdateState.LTC_PENDING, SpsaUpdateState.ACCEPTED}),
    SpsaUpdateState.LTC_PENDING: frozenset({SpsaUpdateState.LTC_RUNNING}),
    SpsaUpdateState.LTC_RUNNING: frozenset({SpsaUpdateState.ACCEPTED, SpsaUpdateState.REVERTED}),
    SpsaUpdateState.ACCEPTED: frozenset({SpsaUpdateState.COMMITTED}),
    SpsaUpdateState.REVERTED: frozenset({SpsaUpdateState.COMMITTED}),
    SpsaUpdateState.COMMITTED: frozenset(),
}


class SpsaLedgerStateError(RuntimeError):
    """Ledger state transitionまたはsealed contractが矛盾している。"""


class SpsaLedgerRuntime:
    """Own run/update/pair transactions over a validated ledger connection。"""

    def __init__(self, connection: sqlite3.Connection, *, run_id: str) -> None:
        self._connection = connection
        self.run_id = run_id

    def initialize_run(
        self,
        *,
        resume_hash: str,
        space_digest: str,
        sealed_run_seed: str,
        contract: JsonObject,
        params: Sequence[ParamEntry],
    ) -> None:
        """Create or strictly validate the sealed run contract and parameters。"""

        expected_contract = _json_text(contract)
        row = self._connection.execute(
            """
            SELECT resume_hash, space_digest, rng_schema, sealed_run_seed, contract_json
            FROM run_contract WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if row is not None:
            if tuple(str(value) for value in row) != (
                resume_hash,
                space_digest,
                SPSA_RNG_SCHEMA,
                sealed_run_seed,
                expected_contract,
            ):
                raise SpsaLedgerStateError(
                    f"SPSA ledger run contract conflict for {self.run_id}; start a fresh run with --no-resume"
                )
            self._validate_parameters(params)
            self._validate_accepted_baseline(params)
            return

        now = _now()
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO run_contract (
                        run_id, contract_schema, resume_hash, space_digest, rng_schema,
                        sealed_run_seed, status, contract_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        "shogiarena.spsa.run-contract.v1",
                        resume_hash,
                        space_digest,
                        SPSA_RNG_SCHEMA,
                        sealed_run_seed,
                        expected_contract,
                        now,
                        now,
                    ),
                )
                for ordinal, param in enumerate(params):
                    self._connection.execute(
                        """
                        INSERT INTO parameters (
                            run_id, parameter_id, ordinal, option_name, parameter_type,
                            minimum_value, maximum_value, initial_value, rounding_policy,
                            schedule_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.run_id,
                            param.name,
                            ordinal,
                            param.engine_option_name,
                            "int" if param.type == "int" else "float",
                            _number_text(param.min),
                            _number_text(param.max),
                            _number_text(param.value),
                            _json_text(
                                {
                                    "value_encoding": param.value_encoding,
                                    "scale": param.scale,
                                    "significant_digits": param.significant_digits,
                                    "rounding": param.rounding,
                                }
                            ),
                            _json_text({"c_end": param.step, "r_end": param.delta}),
                        ),
                    )
                self._connection.execute(
                    """
                    INSERT INTO accepted_baseline (
                        run_id, accepted_update_idx, theta_json, accepted_at, revision
                    ) VALUES (?, 0, ?, ?, 0)
                    """,
                    (
                        self.run_id,
                        _json_text({param.name: float(param.value) for param in params if not param.is_not_used}),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SpsaLedgerStateError(f"Unable to initialize SPSA run contract: {exc}") from exc

    def plan_update(
        self,
        *,
        update_idx: int,
        theta_before: Mapping[str, float],
        schedule: JsonObject,
        ltc_required: bool,
    ) -> None:
        """Persist the update inputs before any pair assignment or dispatch。"""

        now = _now()
        theta_json = _json_text(dict(theta_before))
        schedule_json = _json_text(schedule)
        existing = self._connection.execute(
            """
            SELECT state, theta_before_json, schedule_json, ltc_required
            FROM updates WHERE run_id = ? AND update_idx = ?
            """,
            (self.run_id, update_idx),
        ).fetchone()
        if existing is not None:
            stored_schedule = _parse_json_object(str(existing[2]), label="update schedule")
            requested_schedule = _parse_json_object(schedule_json, label="requested update schedule")
            schedule_matches = all(stored_schedule.get(key) == value for key, value in requested_schedule.items())
            if str(existing[1]) != theta_json or not schedule_matches or int(existing[3]) != int(ltc_required):
                raise SpsaLedgerStateError(f"SPSA update {update_idx} already exists with different inputs")
            return
        if update_idx == 1:
            initial_theta = {
                str(name): float(str(value))
                for name, value in self._connection.execute(
                    """
                    SELECT parameter_id, initial_value FROM parameters
                    WHERE run_id = ? ORDER BY ordinal
                    """,
                    (self.run_id,),
                )
            }
            if theta_json != _json_text(initial_theta):
                raise SpsaLedgerStateError("SPSA update 1 theta does not match the sealed initial baseline")
        if update_idx > 1:
            predecessor = self._connection.execute(
                """
                SELECT state, theta_final_json FROM updates
                WHERE run_id = ? AND update_idx = ?
                """,
                (self.run_id, update_idx - 1),
            ).fetchone()
            if predecessor is None or str(predecessor[0]) != SpsaUpdateState.COMMITTED.value:
                raise SpsaLedgerStateError(
                    f"SPSA update {update_idx} cannot be planned before update {update_idx - 1} is committed"
                )
            if str(predecessor[1]) != theta_json:
                raise SpsaLedgerStateError(
                    f"SPSA update {update_idx} theta does not match committed update {update_idx - 1}"
                )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO updates (
                    run_id, update_idx, state, theta_before_json, schedule_json,
                    ltc_required, created_at, updated_at
                ) VALUES (?, ?, 'PLANNED', ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    update_idx,
                    theta_json,
                    schedule_json,
                    int(ltc_required),
                    now,
                    now,
                ),
            )

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
    ) -> None:
        """Persist one deterministic pair assignment before dispatch。"""

        if assignment_kind not in {"SPSA", "LTC"}:
            raise SpsaLedgerStateError(f"Unsupported SPSA pair assignment kind: {assignment_kind}")
        if assignment_kind == "LTC":
            existing = self._connection.execute(
                "SELECT 1 FROM pair_assignments WHERE run_id = ? AND pair_id = ?",
                (self.run_id, pair_id),
            ).fetchone()
            if existing is None:
                raise SpsaLedgerStateError("New LTC assignments must be persisted by the atomic pair-set operation")
        with self._connection:
            self._assign_pair_row(
                update_idx=update_idx,
                pair_id=pair_id,
                assignment_kind=assignment_kind,
                opening=opening,
                color_assignment=color_assignment,
                flips=flips,
                rounding_samples=rounding_samples,
            )

    def _assign_pair_row(
        self,
        *,
        update_idx: int,
        pair_id: str,
        assignment_kind: str,
        opening: JsonObject,
        color_assignment: JsonObject,
        flips: Mapping[str, int],
        rounding_samples: JsonObject,
    ) -> None:
        payloads = (
            _json_text(opening),
            _json_text(color_assignment),
            _json_text(dict(flips)),
            _json_text(rounding_samples),
        )
        digest = canonical_sha256(
            {
                "run_id": self.run_id,
                "update_idx": update_idx,
                "pair_id": pair_id,
                "assignment_kind": assignment_kind,
                "opening": opening,
                "color_assignment": color_assignment,
                "flips": dict(flips),
                "rounding_samples": rounding_samples,
            }
        )
        existing = self._connection.execute(
            """
            SELECT assignment_kind, assignment_digest, opening_json, color_assignment_json, flip_json,
                   rounding_samples_json
            FROM pair_assignments WHERE run_id = ? AND pair_id = ?
            """,
            (self.run_id, pair_id),
        ).fetchone()
        expected = (assignment_kind, digest, *payloads)
        if existing is not None:
            if tuple(existing) != expected:
                raise SpsaLedgerStateError(f"SPSA pair assignment conflict: {self.run_id}/{pair_id}")
            return
        self._connection.execute(
            """
            INSERT INTO pair_assignments (
                run_id, update_idx, pair_id, assignment_kind, assignment_schema, assignment_digest,
                opening_json, color_assignment_json, flip_json, rounding_samples_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.run_id,
                update_idx,
                pair_id,
                assignment_kind,
                "shogiarena.spsa.pair-assignment.v2",
                digest,
                *payloads,
                _now(),
            ),
        )

    def validate_resume_authority(
        self,
        *,
        resume_hash: str,
        space_digest: str,
        params: Sequence[ParamEntry],
    ) -> None:
        """Validate an existing run without mutating its ledger."""

        row = self._connection.execute(
            """
            SELECT resume_hash, space_digest FROM run_contract
            WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if row != (resume_hash, space_digest):
            raise SpsaLedgerStateError(
                f"SPSA ledger run contract conflict for {self.run_id}; start a fresh run with --no-resume"
            )
        self._validate_parameters(params)
        self._validate_update_theta_chain()
        committed_prefix = self.completed_updates()
        noncontiguous = self._connection.execute(
            """
            SELECT min(update_idx) FROM updates
            WHERE run_id = ? AND state = 'COMMITTED' AND update_idx > ?
            """,
            (self.run_id, committed_prefix),
        ).fetchone()
        if noncontiguous is not None and noncontiguous[0] is not None:
            raise SpsaLedgerStateError(f"SPSA ledger has noncontiguous committed update {int(noncontiguous[0])}")
        self._validate_accepted_baseline(params)
        self.validate_stored_assignments()
        self._validate_all_pair_sets()
        self._validate_terminal_companions()

    def _validate_update_theta_chain(self) -> None:
        expected_theta_json = _json_text(
            {
                str(name): float(str(value))
                for name, value in self._connection.execute(
                    """
                    SELECT parameter_id, initial_value FROM parameters
                    WHERE run_id = ? ORDER BY ordinal
                    """,
                    (self.run_id,),
                )
            }
        )
        expected_update_idx = 1
        has_noncommitted = False
        for update_idx_raw, state_raw, theta_before_json, theta_final_json in self._connection.execute(
            """
            SELECT update_idx, state, theta_before_json, theta_final_json
            FROM updates WHERE run_id = ? ORDER BY update_idx
            """,
            (self.run_id,),
        ):
            update_idx = int(update_idx_raw)
            state = SpsaUpdateState(str(state_raw))
            if update_idx != expected_update_idx:
                raise SpsaLedgerStateError(f"SPSA update theta chain has a gap before update {update_idx}")
            if has_noncommitted:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} exists after an uncommitted predecessor")
            if str(theta_before_json) != expected_theta_json:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} theta chain conflicts with its predecessor")
            if state == SpsaUpdateState.COMMITTED:
                if theta_final_json is None:
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} committed without final theta")
                expected_theta_json = str(theta_final_json)
            else:
                has_noncommitted = True
            expected_update_idx += 1

    def validate_stored_assignments(self) -> None:
        """Recompute every stored assignment digest before resume writes."""

        rows = self._connection.execute(
            """
            SELECT update_idx, pair_id, assignment_kind, assignment_schema, assignment_digest,
                   opening_json, color_assignment_json, flip_json, rounding_samples_json
            FROM pair_assignments WHERE run_id = ?
            ORDER BY update_idx, pair_id
            """,
            (self.run_id,),
        )
        for row in rows:
            update_idx = int(row[0])
            pair_id = str(row[1])
            assignment_kind = str(row[2])
            if str(row[3]) != "shogiarena.spsa.pair-assignment.v2":
                raise SpsaLedgerStateError(f"Unsupported SPSA pair assignment schema: {self.run_id}/{pair_id}")
            opening = _parse_json_object(str(row[5]), label=f"assignment {pair_id} opening")
            color_assignment = _parse_json_object(
                str(row[6]),
                label=f"assignment {pair_id} color assignment",
            )
            flips = _parse_json_object(str(row[7]), label=f"assignment {pair_id} flips")
            rounding_samples = _parse_json_object(
                str(row[8]),
                label=f"assignment {pair_id} rounding samples",
            )
            expected_digest = canonical_sha256(
                {
                    "run_id": self.run_id,
                    "update_idx": update_idx,
                    "pair_id": pair_id,
                    "assignment_kind": assignment_kind,
                    "opening": opening,
                    "color_assignment": color_assignment,
                    "flips": flips,
                    "rounding_samples": rounding_samples,
                }
            )
            if str(row[4]) != expected_digest:
                raise SpsaLedgerStateError(f"SPSA stored pair assignment digest conflict: {self.run_id}/{pair_id}")

    def transition(self, *, update_idx: int, target: SpsaUpdateState) -> None:
        """Apply one legal state transition idempotently。"""

        current = self.update_state(update_idx)
        if current == target:
            return
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise SpsaLedgerStateError(
                f"Invalid SPSA update transition for {update_idx}: {current.value} -> {target.value}"
            )
        if target == SpsaUpdateState.GAMES_RUNNING:
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
        if target == SpsaUpdateState.LTC_RUNNING:
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
        with self._connection:
            self._connection.execute(
                """
                UPDATE updates
                SET state = ?, revision = revision + 1, updated_at = ?
                WHERE run_id = ? AND update_idx = ? AND state = ?
                """,
                (target.value, _now(), self.run_id, update_idx, current.value),
            )

    def update_state(self, update_idx: int) -> SpsaUpdateState:
        row = self._connection.execute(
            "SELECT state FROM updates WHERE run_id = ? AND update_idx = ?",
            (self.run_id, update_idx),
        ).fetchone()
        if row is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} is not planned")
        return SpsaUpdateState(str(row[0]))

    def mark_games_running(self, *, update_idx: int) -> None:
        """Advance a planned update; later durable stages are resume-idempotent。"""

        if self.update_state(update_idx) == SpsaUpdateState.PLANNED:
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
            self.transition(update_idx=update_idx, target=SpsaUpdateState.GAMES_RUNNING)

    def record_variant_quarantine(
        self,
        *,
        update_idx: int,
        pair_id: str,
        variant_id: str,
        failure_classification: str,
    ) -> None:
        """Persist the bounded-retry terminal classification for one variant。"""

        payload: JsonObject = {
            "update_idx": update_idx,
            "pair_id": pair_id,
            "variant_id": variant_id,
            "retry_count": 1,
            "failure_classification": failure_classification,
        }
        payload_json = _json_text(payload)
        existing = self._connection.execute(
            """
            SELECT payload_json FROM event_revisions
            WHERE run_id = ? AND event_type = 'variant_quarantined'
              AND json_extract(payload_json, '$.pair_id') = ?
            """,
            (self.run_id, pair_id),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload_json:
                raise SpsaLedgerStateError(f"SPSA pair {pair_id} quarantine evidence conflicts")
            return
        now = _now()
        revision = self._next_event_revision()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO event_revisions (
                    run_id, revision, event_type, payload_json, created_at
                ) VALUES (?, ?, 'variant_quarantined', ?, ?)
                """,
                (self.run_id, revision, payload_json, now),
            )

    def store_candidate(
        self,
        *,
        update_idx: int,
        theta_candidate: Mapping[str, float],
        schedule: JsonObject,
    ) -> None:
        """Atomically store candidate and advance GAMES_COMPLETE -> CANDIDATE_COMPUTED。"""

        state = self.update_state(update_idx)
        candidate_json = _json_text(dict(theta_candidate))
        schedule_row = self._connection.execute(
            "SELECT schedule_json FROM updates WHERE run_id = ? AND update_idx = ?",
            (self.run_id, update_idx),
        ).fetchone()
        if schedule_row is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} is not planned")
        stored_schedule = _parse_json_object(str(schedule_row[0]), label="stored update schedule")
        if "expected_pair_ids" in schedule and schedule["expected_pair_ids"] != stored_schedule.get(
            "expected_pair_ids"
        ):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} cannot change the sealed expected pair set")
        merged_schedule = {**stored_schedule, **schedule}
        schedule_json = _json_text(merged_schedule)
        if state not in {
            SpsaUpdateState.PLANNED,
            SpsaUpdateState.GAMES_RUNNING,
            SpsaUpdateState.GAMES_COMPLETE,
        }:
            row = self._connection.execute(
                """
                SELECT theta_candidate_json, schedule_json FROM updates
                WHERE run_id = ? AND update_idx = ?
                """,
                (self.run_id, update_idx),
            ).fetchone()
            replay_schedule = _parse_json_object(str(row[1]), label="candidate schedule") if row is not None else {}
            schedule_matches = all(replay_schedule.get(key) == value for key, value in schedule.items())
            if row is None or str(row[0]) != candidate_json or not schedule_matches:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} candidate replay conflict")
            return
        if state != SpsaUpdateState.GAMES_COMPLETE:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} observations are not complete")
        with self._connection:
            self._connection.execute(
                """
                UPDATE updates
                SET state = 'CANDIDATE_COMPUTED', theta_candidate_json = ?,
                    schedule_json = ?,
                    revision = revision + 1, updated_at = ?
                WHERE run_id = ? AND update_idx = ? AND state = 'GAMES_COMPLETE'
                """,
                (
                    candidate_json,
                    schedule_json,
                    _now(),
                    self.run_id,
                    update_idx,
                ),
            )

    def mark_games_complete(self, *, update_idx: int) -> None:
        """Require exactly two valid observations per assigned pair before advancing。"""

        self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
        required_pair_ids = self._required_pair_ids(update_idx=update_idx, assignment_kind="SPSA")
        self._require_valid_observations(
            update_idx=update_idx,
            assignment_kind="SPSA",
            observation_kind="SPSA",
            required_pair_ids=required_pair_ids,
        )
        if self.update_state(update_idx) in {
            SpsaUpdateState.PLANNED,
            SpsaUpdateState.GAMES_RUNNING,
        }:
            self.transition(update_idx=update_idx, target=SpsaUpdateState.GAMES_COMPLETE)

    def commit_without_ltc(
        self,
        *,
        update_idx: int,
        theta_final: Mapping[str, float],
    ) -> None:
        """Commit accepted/final theta in one transaction for a non-LTC update。"""

        state = self.update_state(update_idx)
        final_json = _json_text(dict(theta_final))
        row = self._connection.execute(
            """
            SELECT theta_candidate_json, theta_final_json, ltc_required
            FROM updates WHERE run_id = ? AND update_idx = ?
            """,
            (self.run_id, update_idx),
        ).fetchone()
        if row is None or str(row[0]) != final_json:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} final theta does not match durable candidate")
        self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
        self._require_valid_observations(
            update_idx=update_idx,
            assignment_kind="SPSA",
            observation_kind="SPSA",
            required_pair_ids=self._required_pair_ids(update_idx=update_idx, assignment_kind="SPSA"),
        )
        if state == SpsaUpdateState.COMMITTED:
            if (row[1], row[2]) != (final_json, 0):
                raise SpsaLedgerStateError(f"SPSA update {update_idx} final theta replay conflict")
            return
        if state != SpsaUpdateState.CANDIDATE_COMPUTED:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} candidate is not durable")
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE updates
                SET state = 'COMMITTED', theta_final_json = ?,
                    revision = revision + 2, updated_at = ?
                WHERE run_id = ? AND update_idx = ?
                  AND state = 'CANDIDATE_COMPUTED' AND ltc_required = 0
                """,
                (final_json, _now(), self.run_id, update_idx),
            )
            if cursor.rowcount != 1:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} requires LTC")

    def start_ltc(self, *, update_idx: int) -> None:
        """Advance LTC only after its complete pair assignment set is durable。"""

        state = self.update_state(update_idx)
        if state == SpsaUpdateState.LTC_PENDING:
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
            self.transition(update_idx=update_idx, target=SpsaUpdateState.LTC_RUNNING)
            return
        if state not in {SpsaUpdateState.LTC_RUNNING, SpsaUpdateState.COMMITTED}:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} cannot start LTC from {state.value}")

    def assign_ltc_pairs_and_start(
        self,
        *,
        update_idx: int,
        assignments: Sequence[LedgerPairAssignment],
    ) -> None:
        """Atomically persist the complete LTC assignment set and start dispatch."""

        expected = self._required_pair_ids(update_idx=update_idx, assignment_kind="LTC")
        if tuple(assignment.pair_id for assignment in assignments) != expected:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC assignments do not match the sealed pair set")
        state = self.update_state(update_idx)
        if state in {SpsaUpdateState.LTC_RUNNING, SpsaUpdateState.COMMITTED}:
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
            for assignment in assignments:
                self._assign_pair_row(
                    update_idx=update_idx,
                    pair_id=assignment.pair_id,
                    assignment_kind="LTC",
                    opening=assignment.opening,
                    color_assignment=assignment.color_assignment,
                    flips=assignment.flips,
                    rounding_samples=assignment.rounding_samples,
                )
            return
        if state != SpsaUpdateState.LTC_PENDING:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} cannot assign LTC pairs from {state.value}")
        with self._connection:
            for assignment in assignments:
                self._assign_pair_row(
                    update_idx=update_idx,
                    pair_id=assignment.pair_id,
                    assignment_kind="LTC",
                    opening=assignment.opening,
                    color_assignment=assignment.color_assignment,
                    flips=assignment.flips,
                    rounding_samples=assignment.rounding_samples,
                )
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
            cursor = self._connection.execute(
                """
                UPDATE updates
                SET state = 'LTC_RUNNING', revision = revision + 1, updated_at = ?
                WHERE run_id = ? AND update_idx = ? AND state = 'LTC_PENDING'
                """,
                (_now(), self.run_id, update_idx),
            )
            if cursor.rowcount != 1:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC start transaction conflicted")

    def prepare_ltc(self, *, update_idx: int) -> None:
        """Advance candidate to LTC pending without regressing a resumed stage。"""

        state = self.update_state(update_idx)
        if state == SpsaUpdateState.CANDIDATE_COMPUTED:
            self.transition(update_idx=update_idx, target=SpsaUpdateState.LTC_PENDING)
            return
        if state not in {
            SpsaUpdateState.LTC_PENDING,
            SpsaUpdateState.LTC_RUNNING,
            SpsaUpdateState.COMMITTED,
        }:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} cannot prepare LTC from {state.value}")

    def commit_ltc_decision(
        self,
        *,
        update_idx: int,
        baseline_update_idx: int,
        is_passed: bool,
        evidence: JsonObject,
        accepted_theta: Mapping[str, float],
        reverted_theta: Mapping[str, float],
    ) -> None:
        """Commit decision, final theta, accepted baseline and revision atomically。"""

        decision = "pass" if is_passed else "fail"
        final_theta = dict(accepted_theta if is_passed else reverted_theta)
        decision_digest = canonical_sha256(evidence)
        decision_evidence_json = _json_text(evidence)
        state = self.update_state(update_idx)
        self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
        self._require_valid_observations(
            update_idx=update_idx,
            assignment_kind="SPSA",
            observation_kind="SPSA",
            required_pair_ids=self._required_pair_ids(update_idx=update_idx, assignment_kind="SPSA"),
        )
        expected_ltc_pairs = self._required_pair_ids(update_idx=update_idx, assignment_kind="LTC")
        self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
        pairs_played = self._validate_ltc_decision_evidence(
            evidence=evidence,
            expected_pair_ids=expected_ltc_pairs,
            decision=decision,
            update_idx=update_idx,
        )
        schedule_row = self._connection.execute(
            "SELECT schedule_json FROM updates WHERE run_id = ? AND update_idx = ?",
            (self.run_id, update_idx),
        ).fetchone()
        if schedule_row is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no durable schedule")
        committed_schedule = _parse_json_object(str(schedule_row[0]), label="LTC update schedule")
        stored_pairs_played = committed_schedule.get("ltc_pairs_played")
        if stored_pairs_played is not None and stored_pairs_played != pairs_played:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC played-pair replay conflict")
        committed_schedule["ltc_pairs_played"] = pairs_played
        committed_schedule_json = _json_text(committed_schedule)
        candidate_row = self._connection.execute(
            "SELECT theta_candidate_json FROM updates WHERE run_id = ? AND update_idx = ?",
            (self.run_id, update_idx),
        ).fetchone()
        if is_passed and (candidate_row is None or str(candidate_row[0]) != _json_text(dict(accepted_theta))):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} accepted theta does not match durable candidate")
        if state == SpsaUpdateState.COMMITTED:
            row = self._connection.execute(
                """
                SELECT baseline_update_idx, decision, evidence_digest, evidence_json, final_theta_json
                FROM ltc_decisions
                WHERE run_id = ? AND tested_update_idx = ?
                """,
                (self.run_id, update_idx),
            ).fetchone()
            expected = (
                max(0, baseline_update_idx),
                decision,
                decision_digest,
                decision_evidence_json,
                _json_text(final_theta),
            )
            if row != expected:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC decision replay conflict")
            expected_baseline = (
                (update_idx, dict(accepted_theta)) if is_passed else (max(0, baseline_update_idx), dict(reverted_theta))
            )
            if self.accepted_baseline() != expected_baseline:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} accepted baseline replay conflict")
            return
        if state != SpsaUpdateState.LTC_RUNNING:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC is not running")
        current_baseline_idx, current_baseline_theta = self.accepted_baseline()
        if max(0, baseline_update_idx) != current_baseline_idx:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} baseline index does not match accepted baseline")
        if dict(reverted_theta) != current_baseline_theta:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} revert theta does not match accepted baseline")
        now = _now()
        with self._connection:
            revision = self._next_event_revision()
            self._connection.execute(
                """
                INSERT INTO ltc_decisions (
                    run_id, tested_update_idx, baseline_update_idx, decision,
                    evidence_digest, evidence_json, final_theta_json, decided_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    update_idx,
                    max(0, baseline_update_idx),
                    decision,
                    decision_digest,
                    decision_evidence_json,
                    _json_text(final_theta),
                    now,
                    revision,
                ),
            )
            if is_passed:
                self._connection.execute(
                    """
                    UPDATE accepted_baseline
                    SET accepted_update_idx = ?, theta_json = ?, evidence_digest = ?,
                        accepted_at = ?, revision = ?
                    WHERE run_id = ?
                    """,
                    (
                        update_idx,
                        _json_text(dict(accepted_theta)),
                        decision_digest,
                        now,
                        revision,
                        self.run_id,
                    ),
                )
            self._connection.execute(
                """
                UPDATE updates
                SET state = 'COMMITTED', theta_final_json = ?, schedule_json = ?,
                    revision = revision + 3, updated_at = ?
                WHERE run_id = ? AND update_idx = ? AND state = 'LTC_RUNNING'
                """,
                (_json_text(final_theta), committed_schedule_json, now, self.run_id, update_idx),
            )
            self._connection.execute(
                """
                INSERT INTO event_revisions (
                    run_id, revision, event_type, payload_json, created_at
                ) VALUES (?, ?, 'ltc_decision', ?, ?)
                """,
                (
                    self.run_id,
                    revision,
                    _json_text(
                        {
                            "update_idx": update_idx,
                            "baseline_update_idx": max(0, baseline_update_idx),
                            "decision": decision,
                            "final_theta": final_theta,
                            "evidence_digest": decision_digest,
                        }
                    ),
                    now,
                ),
            )

    def completed_updates(self) -> int:
        """Derive the contiguous committed prefix; never trust a stored counter。"""

        rows = self._connection.execute(
            """
            SELECT update_idx FROM updates
            WHERE run_id = ? AND state = 'COMMITTED'
            ORDER BY update_idx
            """,
            (self.run_id,),
        )
        expected = 1
        for (raw_idx,) in rows:
            idx = int(raw_idx)
            if idx != expected:
                break
            expected += 1
        return expected - 1

    def current_theta(self) -> dict[str, float]:
        """Derive theta from the last contiguous committed update or initial parameters。"""

        completed = self.completed_updates()
        if completed > 0:
            row = self._connection.execute(
                """
                SELECT theta_final_json FROM updates
                WHERE run_id = ? AND update_idx = ? AND state = 'COMMITTED'
                """,
                (self.run_id, completed),
            ).fetchone()
            if row is None or row[0] is None:
                raise SpsaLedgerStateError("Committed SPSA update has no final theta")
            return _parse_theta(str(row[0]))
        rows = self._connection.execute(
            """
            SELECT parameter_id, initial_value FROM parameters
            WHERE run_id = ? ORDER BY ordinal
            """,
            (self.run_id,),
        )
        return {str(name): float(str(value)) for name, value in rows}

    def game_result_kind(self, *, game_id: str) -> str | None:
        """Return the durable observation classification for resume handling。"""

        row = self._connection.execute(
            """
            SELECT result_kind FROM game_observations
            WHERE run_id = ? AND game_id = ?
            """,
            (self.run_id, game_id),
        ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def accepted_baseline(self) -> tuple[int, dict[str, float]]:
        """Restore the current accepted LTC baseline。"""

        row = self._connection.execute(
            """
            SELECT accepted_update_idx, theta_json FROM accepted_baseline
            WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if row is None:
            raise SpsaLedgerStateError("SPSA ledger has no accepted baseline")
        return int(row[0]), _parse_theta(str(row[1]))

    def accepted_best_commit(self, *, update_idx: int) -> JsonObject:
        """Return deterministic promotion evidence from one accepted commit."""

        update_row = self._connection.execute(
            """
            SELECT state, theta_final_json, ltc_required, revision, updated_at
            FROM updates WHERE run_id = ? AND update_idx = ?
            """,
            (self.run_id, update_idx),
        ).fetchone()
        if update_row is None or str(update_row[0]) != SpsaUpdateState.COMMITTED.value:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no committed accepted state")
        theta_raw = update_row[1]
        if theta_raw is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no final theta")
        theta = _parse_theta(str(theta_raw))
        parameter_rows = self._connection.execute(
            """
            SELECT parameter_id, option_name
            FROM parameters WHERE run_id = ? ORDER BY ordinal
            """,
            (self.run_id,),
        ).fetchall()
        if {str(row[0]) for row in parameter_rows} != set(theta):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} final theta does not match parameter identity")
        parameters: list[JsonObject] = [
            {
                "parameter_id": str(parameter_id),
                "option_name": str(option_name),
                "value": theta[str(parameter_id)],
            }
            for parameter_id, option_name in parameter_rows
        ]
        ltc_required = bool(update_row[2])
        ltc_row = self._connection.execute(
            """
            SELECT baseline_update_idx, decision, evidence_digest, decided_at, revision
            FROM ltc_decisions WHERE run_id = ? AND tested_update_idx = ?
            """,
            (self.run_id, update_idx),
        ).fetchone()
        if ltc_required:
            if ltc_row is None or str(ltc_row[1]) != "pass":
                raise SpsaLedgerStateError(f"SPSA update {update_idx} has no accepted LTC decision")
            ltc_decision: JsonObject | None = {
                "tested_update_idx": update_idx,
                "baseline_update_idx": int(ltc_row[0]),
                "decision": "pass",
                "evidence_digest": str(ltc_row[2]),
                "revision": int(ltc_row[4]),
            }
            acceptance = "ltc_pass"
            created_at = str(ltc_row[3])
        else:
            if ltc_row is not None:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} has an unexpected LTC decision")
            ltc_decision = None
            acceptance = "without_ltc"
            created_at = str(update_row[4])
        commit_identity: JsonObject = {
            "run_id": self.run_id,
            "update_idx": update_idx,
            "theta_final": theta,
            "update_revision": int(update_row[3]),
            "created_at": created_at,
            "ltc_decision": ltc_decision,
        }
        return {
            "run_id": self.run_id,
            "update_idx": update_idx,
            "created_at": created_at,
            "acceptance": acceptance,
            "parameters": parameters,
            "update_revision": int(update_row[3]),
            "ltc_decision": ltc_decision,
            "commit_id": canonical_sha256(commit_identity),
        }

    def latest_accepted_update_idx(self) -> int | None:
        """Return the newest committed update eligible for accepted-best."""

        row = self._connection.execute(
            """
            SELECT max(updates.update_idx)
            FROM updates
            WHERE updates.run_id = ?
              AND updates.state = 'COMMITTED'
              AND (
                    updates.ltc_required = 0
                    OR EXISTS (
                        SELECT 1 FROM ltc_decisions
                        WHERE ltc_decisions.run_id = updates.run_id
                          AND ltc_decisions.tested_update_idx = updates.update_idx
                          AND ltc_decisions.decision = 'pass'
                    )
              )
            """,
            (self.run_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def assert_resume_allowed(self) -> bool:
        """Reject permanent terminal runs; return whether a resumable marker exists。"""

        row = self._connection.execute(
            "SELECT reason, resumable FROM terminal_state WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            return False
        reason, resumable = str(row[0]), bool(row[1])
        if reason == "cancelled_resumable" and resumable:
            return True
        raise SpsaLedgerStateError(f"SPSA run is terminal ({reason}); use --no-resume to start a fresh run")

    def resume_disposition(self, *, total_updates: int) -> str:
        """Classify validated restart as dispatch, resumable dispatch, or finalize-only."""

        self._validate_terminal_companions()
        row = self._connection.execute(
            "SELECT reason, resumable FROM terminal_state WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            return "finalize" if self.completed_updates() >= total_updates else "dispatch"
        reason, resumable = str(row[0]), bool(row[1])
        if reason == "cancelled_resumable" and resumable:
            return "resumable"
        if reason in {"completed", "early_stopped"} and not resumable:
            return "finalize"
        raise SpsaLedgerStateError(f"SPSA run is terminal ({reason}); use --no-resume to start a fresh run")

    def invalidate_resumable_terminal(self) -> None:
        """Remove a validated cancellation marker immediately before dispatch。"""

        row = self._connection.execute(
            "SELECT reason, resumable FROM terminal_state WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            return
        if (str(row[0]), int(row[1])) != ("cancelled_resumable", 1):
            raise SpsaLedgerStateError("Only cancelled_resumable terminal state can be invalidated")
        with self._connection:
            self._connection.execute(
                "DELETE FROM terminal_state WHERE run_id = ?",
                (self.run_id,),
            )
            self._connection.execute(
                """
                UPDATE run_contract
                SET status = 'running', revision = revision + 1, updated_at = ?
                WHERE run_id = ?
                """,
                (_now(), self.run_id),
            )

    def commit_terminal(
        self,
        *,
        status: str,
        reason: str,
        resumable: bool,
    ) -> None:
        """Commit terminal reason, pending stage and revision atomically。"""

        completed = self.completed_updates()
        pending_row = self._connection.execute(
            """
            SELECT state FROM updates
            WHERE run_id = ? AND update_idx = ?
            """,
            (self.run_id, completed + 1),
        ).fetchone()
        pending_stage = str(pending_row[0]) if pending_row is not None else None
        existing = self._connection.execute(
            """
            SELECT status, reason, last_committed_update, pending_stage, resumable, revision
            FROM terminal_state WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        expected = (status, reason, completed, pending_stage, int(resumable))
        if existing is not None:
            if tuple(existing[:5]) != expected:
                raise SpsaLedgerStateError(f"SPSA terminal replay conflict for {self.run_id}")
            self._validate_terminal_companions()
            return
        now = _now()
        with self._connection:
            revision = self._next_event_revision()
            self._connection.execute(
                """
                INSERT INTO terminal_state (
                    run_id, status, reason, last_committed_update, pending_stage,
                    resumable, committed_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    reason = excluded.reason,
                    last_committed_update = excluded.last_committed_update,
                    pending_stage = excluded.pending_stage,
                    resumable = excluded.resumable,
                    committed_at = excluded.committed_at,
                    revision = excluded.revision
                """,
                (
                    self.run_id,
                    status,
                    reason,
                    completed,
                    pending_stage,
                    int(resumable),
                    now,
                    revision,
                ),
            )
            self._connection.execute(
                """
                UPDATE run_contract
                SET status = 'terminal', revision = revision + 1, updated_at = ?
                WHERE run_id = ?
                """,
                (now, self.run_id),
            )
            self._connection.execute(
                """
                INSERT INTO event_revisions (
                    run_id, revision, event_type, payload_json, created_at
                ) VALUES (?, ?, 'terminal', ?, ?)
                """,
                (
                    self.run_id,
                    revision,
                    _json_text(
                        {
                            "status": status,
                            "reason": reason,
                            "last_committed_update": completed,
                            "pending_stage": pending_stage,
                            "resumable": resumable,
                        }
                    ),
                    now,
                ),
            )

    def terminal_payload(self) -> JsonObject | None:
        row = self._connection.execute(
            """
            SELECT status, reason, last_committed_update, pending_stage,
                   resumable, committed_at, revision
            FROM terminal_state WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "schema_version": "shogiarena.spsa.terminal.v1",
            "status": str(row[0]),
            "reason": str(row[1]),
            "last_committed_update": int(row[2]),
            "pending_stage": None if row[3] is None else str(row[3]),
            "resumable": bool(row[4]),
            "committed_at": str(row[5]),
            "revision": int(row[6]),
        }

    def completion_status_payload(self, *, cleanup_error: str | None = None) -> JsonObject | None:
        """Build the public terminal contract from durable ledger authority。"""

        terminal = self.terminal_payload()
        if terminal is None:
            return None
        contract_row = self._connection.execute(
            "SELECT resume_hash, space_digest FROM run_contract WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if contract_row is None:
            raise SpsaLedgerStateError("SPSA run contract is missing during terminal projection")
        baseline_idx, baseline_theta = self.accepted_baseline()
        decision_row = self._connection.execute(
            """
            SELECT tested_update_idx, baseline_update_idx, decision,
                   evidence_digest, revision
            FROM ltc_decisions WHERE run_id = ?
            ORDER BY tested_update_idx DESC LIMIT 1
            """,
            (self.run_id,),
        ).fetchone()
        anomaly_row = self._connection.execute(
            """
            SELECT
                sum(CASE WHEN result_kind = 'INCOMPLETE' THEN 1 ELSE 0 END),
                sum(CASE WHEN result_kind = 'FAILED_OBSERVATION' THEN 1 ELSE 0 END)
            FROM game_observations WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        pending_stage = terminal.get("pending_stage")
        last_committed = int(str(terminal["last_committed_update"]))
        payload: JsonObject = {
            "schema_version": 1,
            "status": str(terminal["status"]),
            "termination_reason": str(terminal["reason"]),
            "is_provisional": False,
            "resumable": bool(terminal["resumable"]),
            "last_committed_update": last_committed,
            "pending_update": last_committed + 1 if pending_stage is not None else None,
            "pending_stage": pending_stage,
            "ledger": {
                "schema_version": CURRENT_SPSA_LEDGER_SCHEMA_VERSION,
                "schema_digest": schema_digest(),
            },
            "manifest": {
                "resume_hash": str(contract_row[0]),
                "space_digest": str(contract_row[1]),
            },
            "observation_anomalies": {
                "incomplete": int(anomaly_row[0] or 0) if anomaly_row is not None else 0,
                "failed": int(anomaly_row[1] or 0) if anomaly_row is not None else 0,
            },
            "accepted_baseline": {
                "update_idx": baseline_idx,
                "theta": baseline_theta,
            },
            "last_ltc_decision": (
                None
                if decision_row is None
                else {
                    "tested_update_idx": int(decision_row[0]),
                    "baseline_update_idx": int(decision_row[1]),
                    "decision": str(decision_row[2]),
                    "evidence_digest": str(decision_row[3]),
                    "revision": int(decision_row[4]),
                }
            ),
            "cleanup": {
                "status": "failed" if cleanup_error is not None else "clean",
                "error": cleanup_error,
            },
            "terminal_revision": int(str(terminal["revision"])),
        }
        return payload

    def project_derived_json(self, *, run_dir: Path) -> None:
        """Rebuild compatibility JSON strictly from committed ledger state。"""

        project_spsa_ledger(
            connection=self._connection,
            run_id=self.run_id,
            run_dir=run_dir,
        )

    def _validate_parameters(self, params: Sequence[ParamEntry]) -> None:
        rows = self._connection.execute(
            """
            SELECT parameter_id, ordinal, option_name, parameter_type, minimum_value,
                   maximum_value
            FROM parameters WHERE run_id = ? ORDER BY ordinal
            """,
            (self.run_id,),
        ).fetchall()
        expected = [
            (
                param.name,
                ordinal,
                param.engine_option_name,
                "int" if param.type == "int" else "float",
                _number_text(param.min),
                _number_text(param.max),
            )
            for ordinal, param in enumerate(params)
        ]
        if rows != expected:
            raise SpsaLedgerStateError(
                f"SPSA ledger parameter contract conflict for {self.run_id}; start a fresh run with --no-resume"
            )

    def _validate_accepted_baseline(self, params: Sequence[ParamEntry]) -> None:
        committed_prefix = self.completed_updates()
        baseline_row = self._connection.execute(
            """
            SELECT accepted_update_idx, theta_json, evidence_digest, revision
            FROM accepted_baseline WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if baseline_row is None:
            raise SpsaLedgerStateError(f"SPSA accepted baseline is missing for {self.run_id}")
        latest_pass = self._connection.execute(
            """
            SELECT decisions.tested_update_idx, decisions.final_theta_json,
                   decisions.evidence_digest, decisions.revision, updates.theta_final_json
            FROM ltc_decisions AS decisions
            JOIN updates
              ON updates.run_id = decisions.run_id
             AND updates.update_idx = decisions.tested_update_idx
            WHERE decisions.run_id = ?
              AND decisions.decision = 'pass'
              AND updates.state = 'COMMITTED'
              AND decisions.tested_update_idx <= ?
            ORDER BY decisions.tested_update_idx DESC
            LIMIT 1
            """,
            (self.run_id, committed_prefix),
        ).fetchone()
        if latest_pass is None:
            initial = {param.name: float(param.value) for param in params if not param.is_not_used}
            if (
                int(baseline_row[0]) != 0
                or str(baseline_row[1]) != _json_text(initial)
                or baseline_row[2] is not None
                or int(baseline_row[3]) != 0
            ):
                raise SpsaLedgerStateError("Initial SPSA accepted baseline does not match sealed parameters")
            return
        if (
            int(baseline_row[0]) != int(latest_pass[0])
            or str(baseline_row[1]) != str(latest_pass[1])
            or str(baseline_row[2]) != str(latest_pass[2])
            or int(baseline_row[3]) != int(latest_pass[3])
            or str(latest_pass[4]) != str(latest_pass[1])
        ):
            raise SpsaLedgerStateError(
                "SPSA accepted baseline does not match the latest committed passing LTC decision"
            )

    def _validate_terminal_companions(self) -> None:
        terminal = self._connection.execute(
            """
            SELECT status, reason, last_committed_update, pending_stage, resumable, revision
            FROM terminal_state WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        run_status = self._connection.execute(
            "SELECT status FROM run_contract WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if terminal is None:
            if run_status == ("terminal",):
                raise SpsaLedgerStateError(f"SPSA terminal companion records are inconsistent for {self.run_id}")
            return
        status, reason, completed, pending_stage, resumable, revision = terminal
        event = self._connection.execute(
            """
            SELECT event_type, payload_json FROM event_revisions
            WHERE run_id = ? AND revision = ?
            """,
            (self.run_id, int(revision)),
        ).fetchone()
        expected_payload = _json_text(
            {
                "status": str(status),
                "reason": str(reason),
                "last_committed_update": int(completed),
                "pending_stage": str(pending_stage) if pending_stage is not None else None,
                "resumable": bool(resumable),
            }
        )
        if run_status != ("terminal",) or event != ("terminal", expected_payload):
            raise SpsaLedgerStateError(f"SPSA terminal companion records are inconsistent for {self.run_id}")

    def _required_pair_ids(self, *, update_idx: int, assignment_kind: str) -> tuple[str, ...]:
        row = self._connection.execute(
            "SELECT schedule_json FROM updates WHERE run_id = ? AND update_idx = ?",
            (self.run_id, update_idx),
        ).fetchone()
        if row is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no durable schedule")
        schedule = _parse_json_object(str(row[0]), label=f"update {update_idx} schedule")
        expected_pair_ids = schedule.get("expected_pair_ids")
        if not isinstance(expected_pair_ids, dict):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no sealed expected pair set")
        raw_ids = expected_pair_ids.get(assignment_kind)
        if not isinstance(raw_ids, list) or not all(isinstance(pair_id, str) for pair_id in raw_ids):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has invalid {assignment_kind} expected pair IDs")
        pair_ids = tuple(raw_ids)
        if len(pair_ids) != len(set(pair_ids)):
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has duplicate {assignment_kind} pair IDs")
        if assignment_kind == "SPSA" and not pair_ids:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has an empty SPSA expected pair set")
        return pair_ids

    def _assigned_pair_ids(self, *, update_idx: int, assignment_kind: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            """
            SELECT pair_id FROM pair_assignments
            WHERE run_id = ? AND update_idx = ? AND assignment_kind = ?
            ORDER BY pair_id
            """,
            (self.run_id, update_idx, assignment_kind),
        )
        return tuple(str(row[0]) for row in rows)

    def _require_exact_pair_set(self, *, update_idx: int, assignment_kind: str) -> None:
        expected = tuple(sorted(self._required_pair_ids(update_idx=update_idx, assignment_kind=assignment_kind)))
        actual = self._assigned_pair_ids(update_idx=update_idx, assignment_kind=assignment_kind)
        if actual != expected:
            raise SpsaLedgerStateError(
                f"SPSA update {update_idx} {assignment_kind} assignment set is incomplete or unexpected; "
                f"expected={len(expected)}, actual={len(actual)}"
            )

    def _validate_all_pair_sets(self) -> None:
        rows = self._connection.execute(
            """
            SELECT update_idx, state, ltc_required, schedule_json
            FROM updates WHERE run_id = ? ORDER BY update_idx
            """,
            (self.run_id,),
        )
        for update_idx_raw, state_raw, ltc_required_raw, schedule_json in rows:
            update_idx = int(update_idx_raw)
            state = SpsaUpdateState(str(state_raw))
            expected_spsa = set(self._required_pair_ids(update_idx=update_idx, assignment_kind="SPSA"))
            actual_spsa = set(self._assigned_pair_ids(update_idx=update_idx, assignment_kind="SPSA"))
            actual_ltc = set(self._assigned_pair_ids(update_idx=update_idx, assignment_kind="LTC"))
            if state == SpsaUpdateState.PLANNED:
                if not actual_spsa.issubset(expected_spsa) or actual_ltc:
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} PLANNED pair set is invalid")
                observation_count = self._connection.execute(
                    "SELECT count(*) FROM game_observations WHERE run_id = ? AND update_idx = ?",
                    (self.run_id, update_idx),
                ).fetchone()
                if observation_count is None or int(observation_count[0]) != 0:
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} PLANNED state has game observations")
                continue
            self._require_exact_pair_set(update_idx=update_idx, assignment_kind="SPSA")
            if state not in {SpsaUpdateState.GAMES_RUNNING}:
                expected_spsa_ordered = self._required_pair_ids(
                    update_idx=update_idx,
                    assignment_kind="SPSA",
                )
                self._require_valid_observations(
                    update_idx=update_idx,
                    assignment_kind="SPSA",
                    observation_kind="SPSA",
                    required_pair_ids=expected_spsa_ordered,
                )
            is_ltc = bool(ltc_required_raw)
            if (
                state
                in {
                    SpsaUpdateState.LTC_RUNNING,
                    SpsaUpdateState.ACCEPTED,
                    SpsaUpdateState.REVERTED,
                    SpsaUpdateState.COMMITTED,
                }
                and is_ltc
            ):
                self._require_exact_pair_set(update_idx=update_idx, assignment_kind="LTC")
            elif actual_ltc:
                raise SpsaLedgerStateError(f"SPSA update {update_idx} has LTC assignments before atomic start")
            if state == SpsaUpdateState.COMMITTED and is_ltc:
                schedule = _parse_json_object(str(schedule_json), label=f"update {update_idx} schedule")
                expected_ltc = self._required_pair_ids(update_idx=update_idx, assignment_kind="LTC")
                decision_row = self._connection.execute(
                    """
                    SELECT decision, evidence_digest, evidence_json
                    FROM ltc_decisions
                    WHERE run_id = ? AND tested_update_idx = ?
                    """,
                    (self.run_id, update_idx),
                ).fetchone()
                if decision_row is None:
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} has no durable LTC decision evidence")
                evidence_json = str(decision_row[2])
                evidence = _parse_json_object(
                    evidence_json,
                    label=f"update {update_idx} LTC decision evidence",
                )
                if evidence_json != _json_text(evidence):
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC decision evidence is not canonical")
                if canonical_sha256(evidence) != str(decision_row[1]):
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC decision evidence digest mismatch")
                pairs_played = self._validate_ltc_decision_evidence(
                    evidence=evidence,
                    expected_pair_ids=expected_ltc,
                    decision=str(decision_row[0]),
                    update_idx=update_idx,
                )
                if schedule.get("ltc_pairs_played") != pairs_played:
                    raise SpsaLedgerStateError(f"SPSA update {update_idx} LTC played-pair evidence conflicts")

    def _validate_ltc_decision_evidence(
        self,
        *,
        evidence: JsonObject,
        expected_pair_ids: Sequence[str],
        decision: str,
        update_idx: int,
    ) -> int:
        expected_total = len(expected_pair_ids)
        evidence_update_idx = evidence.get("update_idx")
        if (
            isinstance(evidence_update_idx, bool)
            or not isinstance(evidence_update_idx, int)
            or evidence_update_idx != update_idx
        ):
            raise SpsaLedgerStateError("LTC evidence update_idx does not match the committed update")
        total_pairs = evidence.get("total_pairs")
        pairs_played = evidence.get("pairs_played")
        if isinstance(total_pairs, bool) or not isinstance(total_pairs, int) or total_pairs != expected_total:
            raise SpsaLedgerStateError("LTC evidence total_pairs does not match the sealed pair set")
        if isinstance(pairs_played, bool) or not isinstance(pairs_played, int):
            raise SpsaLedgerStateError("LTC evidence has no valid pairs_played")
        if pairs_played < 1 or pairs_played > expected_total:
            raise SpsaLedgerStateError("LTC evidence pairs_played is outside the sealed pair set")
        total_games = evidence.get("total_games")
        if isinstance(total_games, bool) or not isinstance(total_games, int) or total_games != pairs_played * 2:
            raise SpsaLedgerStateError("LTC evidence total_games does not match the played pair count")
        played_pair_ids = tuple(expected_pair_ids[:pairs_played])
        self._require_valid_observations(
            update_idx=update_idx,
            assignment_kind="LTC",
            observation_kind="LTC",
            required_pair_ids=played_pair_ids,
        )
        (
            expected_counts,
            expected_sprt,
            expected_status,
            expected_fail_reasons,
            expected_winrate,
            expected_elo,
            expected_average_score,
        ) = self._derive_ltc_decision_authority(
            update_idx=update_idx,
            played_pair_ids=played_pair_ids,
            sealed_total_pairs=expected_total,
        )
        for field, expected_count in expected_counts.items():
            if evidence.get(field) != expected_count:
                raise SpsaLedgerStateError(f"LTC evidence {field} conflicts with durable observations")
        if evidence.get("winrate") != expected_winrate or evidence.get("elo") != expected_elo:
            raise SpsaLedgerStateError("LTC evidence metrics conflict with durable observations")
        if evidence.get("average_score") != expected_average_score:
            raise SpsaLedgerStateError("LTC evidence average_score conflicts with durable observations")
        if evidence.get("status") != expected_status:
            raise SpsaLedgerStateError("LTC evidence status conflicts with durable observations and criteria")
        if evidence.get("fail_reasons") != expected_fail_reasons:
            raise SpsaLedgerStateError("LTC evidence failure reasons conflict with durable observations and criteria")
        expected_decision = "pass" if expected_status == "passed" else "fail"
        if expected_status not in {"passed", "failed"} or decision != expected_decision:
            raise SpsaLedgerStateError("LTC committed decision conflicts with durable observations and criteria")
        if evidence.get("is_accepted") is not (expected_status == "passed"):
            raise SpsaLedgerStateError("LTC evidence acceptance conflicts with durable observations and criteria")
        sprt_decision = evidence.get("sprt_decision")
        if sprt_decision not in {None, "continue", "accept_h0", "accept_h1"}:
            raise SpsaLedgerStateError("LTC evidence has an invalid SPRT decision")
        sprt = evidence.get("sprt")
        if sprt != expected_sprt:
            raise SpsaLedgerStateError("LTC evidence SPRT payload conflicts with durable observations and criteria")
        expected_sprt_decision = expected_sprt.get("decision") if expected_sprt is not None else None
        if sprt_decision != expected_sprt_decision:
            raise SpsaLedgerStateError("LTC evidence SPRT decision conflicts with durable observations and criteria")
        if pairs_played < expected_total and sprt_decision not in {"accept_h0", "accept_h1"}:
            raise SpsaLedgerStateError("LTC early termination requires a terminal SPRT decision")
        return pairs_played

    def _derive_ltc_decision_authority(
        self,
        *,
        update_idx: int,
        played_pair_ids: Sequence[str],
        sealed_total_pairs: int,
    ) -> tuple[dict[str, int], JsonObject | None, str, list[str], float, float | None, float]:
        criteria = self._sealed_ltc_pass_criteria()
        pair_scores = [
            self._durable_ltc_pair_scores(update_idx=update_idx, pair_id=pair_id) for pair_id in played_pair_ids
        ]
        tuned_wins = sum(score == 1.0 for pair in pair_scores for score in pair)
        baseline_wins = sum(score == 0.0 for pair in pair_scores for score in pair)
        draws = sum(score == 0.5 for pair in pair_scores for score in pair)
        total_games = tuned_wins + baseline_wins + draws
        winrate = (tuned_wins + 0.5 * draws) / total_games
        elo = -400 * math.log10(1 / winrate - 1) if 0.0 < winrate < 1.0 else None
        average_score = sum(2.0 * score - 1.0 for pair in pair_scores for score in pair) / total_games
        sprt_payload = self._derive_ltc_sprt(criteria=criteria, pair_scores=pair_scores)
        sprt_decision_raw = sprt_payload.get("decision") if sprt_payload is not None else None
        sprt_decision = SprtDecision(str(sprt_decision_raw)) if sprt_decision_raw is not None else None
        status, fail_reasons = determine_ltc_status(
            criteria,
            winrate=winrate,
            elo=elo,
            sprt_payload=sprt_payload,
            sprt_decision=sprt_decision,
        )
        status, fail_reasons = fail_closed_ltc_status_at_budget(
            status,
            fail_reasons,
            pairs_played=len(pair_scores),
            total_pairs=sealed_total_pairs,
        )
        return (
            {
                "total_games": total_games,
                "tuned_wins": tuned_wins,
                "baseline_wins": baseline_wins,
                "draws": draws,
            },
            sprt_payload,
            status,
            fail_reasons,
            winrate,
            elo,
            average_score,
        )

    def _sealed_ltc_pass_criteria(self) -> JsonObject:
        row = self._connection.execute(
            "SELECT contract_json FROM run_contract WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            raise SpsaLedgerStateError(f"SPSA run contract is missing for {self.run_id}")
        contract = _parse_json_object(str(row[0]), label="run contract")
        config = contract.get("config")
        if not isinstance(config, dict):
            raise SpsaLedgerStateError("SPSA run contract has no sealed config")
        ltc = config.get("ltc_regression")
        if not isinstance(ltc, dict):
            raise SpsaLedgerStateError("SPSA run contract has no sealed LTC config")
        criteria = ltc.get("pass_criteria")
        if criteria is None:
            return {}
        if not isinstance(criteria, dict):
            raise SpsaLedgerStateError("SPSA run contract has invalid sealed LTC pass criteria")
        return {str(key): value for key, value in criteria.items()}

    def _durable_ltc_pair_scores(self, *, update_idx: int, pair_id: str) -> tuple[float, float]:
        row = self._connection.execute(
            """
            SELECT color_assignment_json FROM pair_assignments
            WHERE run_id = ? AND update_idx = ? AND pair_id = ? AND assignment_kind = 'LTC'
            """,
            (self.run_id, update_idx, pair_id),
        ).fetchone()
        if row is None:
            raise SpsaLedgerStateError(f"SPSA update {update_idx} has no LTC assignment {pair_id}")
        assignment = _parse_json_object(str(row[0]), label=f"LTC pair {pair_id} color assignment")
        games = assignment.get("games")
        if not isinstance(games, list) or len(games) != 2:
            raise SpsaLedgerStateError(f"LTC pair {pair_id} has invalid color assignment")
        observations = self._validated_pair_outcomes(
            update_idx=update_idx,
            assignment_kind="LTC",
            observation_kind="LTC",
            required_pair_ids=(pair_id,),
        )[pair_id]
        scores: dict[str, float] = {}
        for game in games:
            if not isinstance(game, dict):
                raise SpsaLedgerStateError(f"LTC pair {pair_id} has invalid game assignment")
            game_id = game.get("game_id")
            tuned_as = game.get("tuned_as")
            if not isinstance(game_id, str) or tuned_as not in {"black", "white"} or tuned_as in scores:
                raise SpsaLedgerStateError(f"LTC pair {pair_id} has invalid game identity")
            result_kind = observations.pop(game_id, None)
            if result_kind is None:
                raise SpsaLedgerStateError(f"LTC pair {pair_id} has no durable observation for {game_id}")
            if result_kind == "DRAW":
                score = 0.5
            elif result_kind == "BLACK_WIN":
                score = 1.0 if tuned_as == "black" else 0.0
            elif result_kind == "WHITE_WIN":
                score = 1.0 if tuned_as == "white" else 0.0
            else:
                raise SpsaLedgerStateError(f"LTC pair {pair_id} has invalid durable outcome")
            scores[str(tuned_as)] = score
        if observations or set(scores) != {"black", "white"}:
            raise SpsaLedgerStateError(f"LTC pair {pair_id} durable game identities conflict")
        return scores["black"], scores["white"]

    def _derive_ltc_sprt(
        self,
        *,
        criteria: JsonObject,
        pair_scores: Sequence[tuple[float, float]],
    ) -> JsonObject | None:
        sprt_config = criteria.get("sprt")
        if sprt_config is None:
            return None
        if not isinstance(sprt_config, dict):
            raise SpsaLedgerStateError("Sealed LTC SPRT config is invalid")
        model = sprt_config.get("model", "gsprt-trinomial-v1")
        min_games = sprt_config.get("min_games", 0)
        if not isinstance(model, str) or isinstance(min_games, bool) or not isinstance(min_games, int):
            raise SpsaLedgerStateError("Sealed LTC SPRT model or min_games is invalid")
        sprt = Sprt(
            elo0=_sealed_number(sprt_config, "elo0", default=0.0),
            elo1=_sealed_number(sprt_config, "elo1", default=5.0),
            alpha=_sealed_number(sprt_config, "alpha", default=0.05),
            beta=_sealed_number(sprt_config, "beta", default=0.05),
            model=model,
            min_pairs=max(PENTANOMIAL_MIN_PAIRS_FOR_LLR, math.ceil(min_games / 2)),
        )
        result = sprt.get_status()
        for pair_index, (black_score, white_score) in enumerate(pair_scores):
            if model == SPRT_MODEL_GSPRT_PENTANOMIAL:
                result = sprt.add_paired_observation(black_score=black_score, white_score=white_score)
            else:
                result = sprt.add_game_result(_score_to_sprt_result(black_score))
                result = sprt.add_game_result(_score_to_sprt_result(white_score))
            effective_decision = result.decision if result.games_played >= min_games else SprtDecision.CONTINUE
            if effective_decision != SprtDecision.CONTINUE and pair_index + 1 < len(pair_scores):
                raise SpsaLedgerStateError("LTC durable observations continue beyond the sealed SPRT terminal")
        effective_decision = result.decision if result.games_played >= min_games else SprtDecision.CONTINUE
        return _sprt_evidence_payload(result, decision=effective_decision)

    def _require_valid_observations(
        self,
        *,
        update_idx: int,
        assignment_kind: str,
        observation_kind: str,
        required_pair_ids: Sequence[str],
    ) -> None:
        self._validated_pair_outcomes(
            update_idx=update_idx,
            assignment_kind=assignment_kind,
            observation_kind=observation_kind,
            required_pair_ids=required_pair_ids,
        )

    def _validated_pair_outcomes(
        self,
        *,
        update_idx: int,
        assignment_kind: str,
        observation_kind: str,
        required_pair_ids: Sequence[str],
    ) -> dict[str, dict[str, str]]:
        """Bind each valid outcome to one sealed color assignment and one bounded retry。"""

        required = tuple(required_pair_ids)
        if not required:
            raise SpsaLedgerStateError(f"{assignment_kind} update {update_idx} requires at least one assigned pair")
        assignment_rows = self._connection.execute(
            """
            SELECT pair_id, color_assignment_json
            FROM pair_assignments
            WHERE run_id = ? AND update_idx = ? AND assignment_kind = ?
            """,
            (self.run_id, update_idx, assignment_kind),
        ).fetchall()
        assignments = {str(pair_id): str(color_json) for pair_id, color_json in assignment_rows}
        outcomes: dict[str, dict[str, str]] = {}
        valid_kinds = {"BLACK_WIN", "WHITE_WIN", "DRAW"}
        for pair_id in required:
            color_json = assignments.get(pair_id)
            if color_json is None:
                raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} has no sealed assignment")
            color_assignment = _parse_json_object(color_json, label=f"{assignment_kind} pair {pair_id}")
            games = color_assignment.get("games")
            if not isinstance(games, list) or len(games) != 2:
                raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} has invalid color assignment")
            original_ids: list[str] = []
            slots: set[str] = set()
            tuned_sides: set[str] = set()
            for game in games:
                if not isinstance(game, dict):
                    raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} has invalid game assignment")
                game_id = game.get("game_id")
                slot = game.get("slot")
                tuned_as = game.get("tuned_as")
                if not isinstance(game_id, str) or slot not in {"black", "white"} or tuned_as not in {"black", "white"}:
                    raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} has invalid game identity")
                original_ids.append(game_id)
                slots.add(str(slot))
                tuned_sides.add(str(tuned_as))
            if len(set(original_ids)) != 2 or slots != {"black", "white"} or tuned_sides != {"black", "white"}:
                raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} has duplicate color identity")

            observed = {
                str(game_id): str(result_kind)
                for game_id, result_kind in self._connection.execute(
                    """
                    SELECT game_id, result_kind FROM game_observations
                    WHERE run_id = ? AND update_idx = ? AND pair_id = ? AND observation_kind = ?
                    """,
                    (self.run_id, update_idx, pair_id, observation_kind),
                )
            }
            allowed_ids = set(original_ids)
            allowed_ids.update(f"{game_id}-retry1" for game_id in original_ids)
            unexpected = set(observed).difference(allowed_ids)
            if unexpected:
                raise SpsaLedgerStateError(
                    f"{assignment_kind} pair {pair_id} has unexpected observation identities: {sorted(unexpected)}"
                )

            pair_outcomes: dict[str, str] = {}
            failed_originals = 0
            for game_id in original_ids:
                original_result = observed.get(game_id)
                retry_id = f"{game_id}-retry1"
                retry_result = observed.get(retry_id)
                if original_result in valid_kinds:
                    if retry_result is not None:
                        raise SpsaLedgerStateError(
                            f"{assignment_kind} pair {pair_id} retries a valid observation: {game_id}"
                        )
                    pair_outcomes[game_id] = original_result
                    continue
                if original_result != "FAILED_OBSERVATION":
                    raise SpsaLedgerStateError(
                        f"{assignment_kind} pair {pair_id} has no valid assigned observation: {game_id}"
                    )
                failed_originals += 1
                if retry_result not in valid_kinds:
                    raise SpsaLedgerStateError(
                        f"{assignment_kind} pair {pair_id} has no valid bounded retry: {retry_id}"
                    )
                pair_outcomes[game_id] = retry_result
            if failed_originals > 1:
                raise SpsaLedgerStateError(f"{assignment_kind} pair {pair_id} exceeds the one-retry budget")
            outcomes[pair_id] = pair_outcomes
        return outcomes

    def _next_event_revision(self) -> int:
        row = self._connection.execute(
            "SELECT coalesce(max(revision), 0) + 1 FROM event_revisions WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 1


def _json_text(value: HashInput) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _number_text(value: float) -> str:
    return format(float(value), ".17g")


def _sealed_number(payload: Mapping[str, object], field: str, *, default: float) -> float:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SpsaLedgerStateError(f"Sealed LTC SPRT {field} is invalid")
    return float(value)


def _score_to_sprt_result(score: float) -> GameResult:
    if score == 1.0:
        return GameResult.WHITE_WIN
    if score == 0.0:
        return GameResult.BLACK_WIN
    if score == 0.5:
        return GameResult.DRAW_BY_REPETITION
    raise SpsaLedgerStateError(f"Unsupported LTC tested score: {score}")


def _sprt_evidence_payload(result: SprtResult, *, decision: SprtDecision) -> JsonObject:
    return {
        "llr": result.llr,
        "lower": result.lower_bound,
        "upper": result.upper_bound,
        "decision": decision.value,
        "games": result.games_played,
        "wins": result.wins,
        "draws": result.draws,
        "losses": result.losses,
        "winrate": result.win_rate,
        "elo": result.elo_estimate,
    }


def _parse_theta(raw: str) -> dict[str, float]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SpsaLedgerStateError("SPSA theta payload is not an object")
    theta: dict[str, float] = {}
    for name, number in value.items():
        if not isinstance(name, str) or not isinstance(number, int | float):
            raise SpsaLedgerStateError("SPSA theta payload contains invalid values")
        theta[name] = float(number)
    return theta


def _parse_json_object(raw: str, *, label: str) -> JsonObject:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SpsaLedgerStateError(f"SPSA {label} is not an object")
    return {str(key): item for key, item in value.items()}


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["SpsaLedgerRuntime", "SpsaLedgerStateError", "SpsaUpdateState"]
