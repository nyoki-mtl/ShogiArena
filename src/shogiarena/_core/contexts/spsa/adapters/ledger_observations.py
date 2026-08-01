"""game.dbとSPSA ledgerのobservation reconciliation。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerGameObservation
from shogiarena._core.contexts.spsa.domain.participation_identity import (
    parse_spsa_participation_identity,
)
from shogiarena._core.shared.kernel.database_types import DatabaseServicePort, SpsaGameDatabaseRecord
from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256


class SpsaObservationIntegrityError(RuntimeError):
    """Cross-store observation identityまたはevidenceが矛盾している。"""


def build_ledger_observation(record: SpsaGameDatabaseRecord) -> LedgerGameObservation:
    """game.db recordをcoercionなしでledger observationへ変換する。"""

    identity = parse_spsa_participation_identity(record.participation_extras)
    result_kind = _result_kind(record.result)
    evidence_digest = canonical_sha256(
        {
            "run_id": identity.run_id,
            "game_id": record.game_id,
            "update_idx": identity.update_idx,
            "pair_id": identity.pair_id,
            "attempt_id": identity.attempt_id,
            "observation_kind": identity.observation_kind,
            "result_kind": result_kind,
            "game_db_id": record.game_db_id,
        }
    )
    return LedgerGameObservation(
        run_id=identity.run_id,
        game_id=record.game_id,
        update_idx=identity.update_idx,
        pair_id=identity.pair_id,
        attempt_id=identity.attempt_id,
        observation_kind=identity.observation_kind,
        result_kind=result_kind,
        game_db_id=record.game_db_id,
        evidence_digest=evidence_digest,
        observed_at=datetime.now(UTC).isoformat(),
    )


class SpsaObservationLedger:
    """Observation insert/reconciliation repository。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, observation: LedgerGameObservation) -> bool:
        """Insert one observation idempotently; conflicting reuse fails closed。"""

        self._validate_pair_assignment(observation)
        existing = self._load_one(observation.run_id, observation.game_id)
        if existing is not None:
            self._require_same_evidence(existing, observation)
            return False
        duplicate_attempt = self._connection.execute(
            """
            SELECT game_id FROM game_observations
            WHERE run_id = ? AND pair_id = ? AND attempt_id = ?
            """,
            (observation.run_id, observation.pair_id, observation.attempt_id),
        ).fetchone()
        if duplicate_attempt is not None:
            raise SpsaObservationIntegrityError(
                "Duplicate SPSA attempt identity maps to different game records: "
                f"{observation.run_id}/{observation.pair_id}/{observation.attempt_id}"
            )
        self._connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.run_id,
                observation.game_id,
                observation.update_idx,
                observation.pair_id,
                observation.attempt_id,
                observation.observation_kind,
                observation.result_kind,
                observation.game_db_id,
                observation.evidence_digest,
                observation.observed_at,
            ),
        )
        return True

    def insert_committed(self, observation: LedgerGameObservation) -> bool:
        """Commit one observation independently after game.db authority is durable。"""

        try:
            with self._connection:
                return self.insert(observation)
        except sqlite3.IntegrityError as exc:
            raise SpsaObservationIntegrityError(f"SPSA observation constraint violation: {exc}") from exc

    def reconcile(self, *, run_id: str, source: DatabaseServicePort) -> int:
        """Validate both stores, then insert only missing game.db observations。"""

        missing = self._validated_missing(run_id=run_id, source=source)
        try:
            with self._connection:
                for observation in missing:
                    self.insert(observation)
        except sqlite3.IntegrityError as exc:
            raise SpsaObservationIntegrityError(f"SPSA observation constraint violation: {exc}") from exc
        return len(missing)

    def validate_reconciliation(self, *, run_id: str, source: DatabaseServicePort) -> int:
        """Validate cross-store evidence without inserting missing observations."""

        return len(self._validated_missing(run_id=run_id, source=source))

    def _validated_missing(
        self,
        *,
        run_id: str,
        source: DatabaseServicePort,
    ) -> list[LedgerGameObservation]:
        source_observations = [
            build_ledger_observation(record) for record in source.get_spsa_game_database_records(run_id=run_id)
        ]
        for observation in source_observations:
            if observation.run_id != run_id:
                raise SpsaObservationIntegrityError(
                    f"game.db SPSA identity belongs to {observation.run_id}, expected {run_id}"
                )
            self._validate_pair_assignment(observation)
        by_game = {observation.game_id: observation for observation in source_observations}
        if len(by_game) != len(source_observations):
            raise SpsaObservationIntegrityError("Duplicate SPSA game records exist in game.db")

        ledger_rows = self._load_all(run_id)
        for existing in ledger_rows:
            source_observation = by_game.get(existing.game_id)
            if source_observation is None:
                raise SpsaObservationIntegrityError(
                    f"Ledger-only SPSA observation has no game.db authority: {run_id}/{existing.game_id}"
                )
            self._require_same_evidence(existing, source_observation)

        missing = [
            observation for observation in source_observations if self._load_one(run_id, observation.game_id) is None
        ]
        return missing

    def _validate_pair_assignment(self, observation: LedgerGameObservation) -> None:
        row = self._connection.execute(
            """
            SELECT update_idx, assignment_kind FROM pair_assignments
            WHERE run_id = ? AND pair_id = ?
            """,
            (observation.run_id, observation.pair_id),
        ).fetchone()
        if row is None:
            raise SpsaObservationIntegrityError(
                f"SPSA observation references unknown pair: {observation.run_id}/{observation.pair_id}"
            )
        if (int(row[0]), str(row[1])) != (observation.update_idx, observation.observation_kind):
            raise SpsaObservationIntegrityError(
                "SPSA observation update/kind does not match pair assignment: "
                f"{observation.run_id}/{observation.pair_id}"
            )

    def _load_one(self, run_id: str, game_id: str) -> LedgerGameObservation | None:
        row = self._connection.execute(
            """
            SELECT run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                   game_db_id, evidence_digest, observed_at
            FROM game_observations WHERE run_id = ? AND game_id = ?
            """,
            (run_id, game_id),
        ).fetchone()
        return _observation_from_row(row) if row is not None else None

    def _load_all(self, run_id: str) -> list[LedgerGameObservation]:
        rows = self._connection.execute(
            """
            SELECT run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                   game_db_id, evidence_digest, observed_at
            FROM game_observations WHERE run_id = ? ORDER BY game_db_id
            """,
            (run_id,),
        )
        return [_observation_from_row(row) for row in rows]

    @staticmethod
    def _require_same_evidence(
        existing: LedgerGameObservation,
        source: LedgerGameObservation,
    ) -> None:
        if (
            existing.run_id,
            existing.game_id,
            existing.update_idx,
            existing.pair_id,
            existing.attempt_id,
            existing.observation_kind,
            existing.result_kind,
            existing.game_db_id,
            existing.evidence_digest,
        ) != (
            source.run_id,
            source.game_id,
            source.update_idx,
            source.pair_id,
            source.attempt_id,
            source.observation_kind,
            source.result_kind,
            source.game_db_id,
            source.evidence_digest,
        ):
            raise SpsaObservationIntegrityError(f"SPSA observation evidence conflict: {source.run_id}/{source.game_id}")


def _observation_from_row(row: tuple[object, ...]) -> LedgerGameObservation:
    return LedgerGameObservation(
        run_id=str(row[0]),
        game_id=str(row[1]),
        update_idx=int(str(row[2])),
        pair_id=str(row[3]),
        attempt_id=str(row[4]),
        observation_kind=str(row[5]),
        result_kind=str(row[6]),
        game_db_id=int(str(row[7])),
        evidence_digest=str(row[8]),
        observed_at=str(row[9]),
    )


def _result_kind(result: GameResult) -> str:
    if result.is_black_win():
        return "BLACK_WIN"
    if result.is_white_win():
        return "WHITE_WIN"
    if result.is_draw():
        return "DRAW"
    if result == GameResult.PAUSED:
        return "INCOMPLETE"
    if result in {GameResult.ERROR, GameResult.INVALID}:
        return "FAILED_OBSERVATION"
    raise SpsaObservationIntegrityError(f"Unsupported SPSA game result: {game_result_name(result)}")


__all__ = [
    "LedgerGameObservation",
    "SpsaObservationIntegrityError",
    "SpsaObservationLedger",
    "build_ledger_observation",
]
