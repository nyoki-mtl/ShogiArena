from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from typing import Literal

import pytest
import rsshogi

from shogiarena._core.contexts.spsa.adapters.ledger_observations import (
    SpsaObservationIntegrityError,
    SpsaObservationLedger,
    build_ledger_observation,
)
from shogiarena._core.contexts.spsa.adapters.ledger_schema import create_canonical_schema
from shogiarena._core.contexts.spsa.domain.participation_identity import (
    SpsaParticipationIdentity,
    identity_extra,
)
from shogiarena._core.shared.kernel.database_types import (
    GameRecordPlayers,
    SpsaGameDatabaseRecord,
)
from shogiarena._core.shared.kernel.game_results import GameResult


class _ObservationSource:
    def __init__(self, records: Sequence[SpsaGameDatabaseRecord]) -> None:
        self.records = list(records)

    def get_spsa_game_database_records(self, *, run_id: str) -> Sequence[SpsaGameDatabaseRecord]:
        del run_id
        return list(self.records)

    def ensure_schema(self) -> None: ...

    def close(self) -> None: ...

    def append_record_list(
        self,
        record_list: Iterable[rsshogi.record.Record | None],
        *,
        should_update: bool = False,
    ) -> None:
        del record_list, should_update

    def append_record_with_participation(
        self,
        record: rsshogi.record.Record,
        *,
        participation: Iterable[object],
    ) -> int:
        del record, participation
        return 1

    def get_game_id_by_name(self, game_name: str) -> int | None:
        del game_name
        return None

    def load_record(
        self,
        *,
        game_id: int | None = None,
        game_name: str | None = None,
    ) -> rsshogi.record.Record | None:
        del game_id, game_name
        return None

    def record_game_participation(self, *, game_id: int, participation: Iterable[object]) -> None:
        del game_id, participation

    def get_games_with_players(self, *, game_type: str) -> Sequence[GameRecordPlayers]:
        del game_type
        return []


def _open_ledger() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    create_canonical_schema(connection)
    now = "2026-07-27T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO run_contract (
            run_id, contract_schema, resume_hash, space_digest, rng_schema,
            sealed_run_seed, status, contract_json, created_at, updated_at
        ) VALUES ('run-1', 'v1', 'resume', 'space', 'rng', 'seed', 'running', '{}', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO updates (
            run_id, update_idx, state, theta_before_json, ltc_required, created_at, updated_at
        ) VALUES ('run-1', 1, 'GAMES_RUNNING', '{}', 0, ?, ?)
        """,
        (now, now),
    )
    for pair_id in ("pair-1", "pair-2"):
        connection.execute(
            """
            INSERT INTO pair_assignments (
                run_id, update_idx, pair_id, assignment_kind, assignment_schema, assignment_digest,
                opening_json, color_assignment_json, flip_json, rounding_samples_json, created_at
            ) VALUES ('run-1', 1, ?, 'SPSA', 'v1', ?, '{}', '{}', '{}', '{}', ?)
            """,
            (pair_id, f"digest-{pair_id}", now),
        )
    connection.commit()
    return connection


def _record(
    *,
    game_db_id: int = 1,
    game_id: str = "game-1",
    pair_id: str = "pair-1",
    attempt_id: str = "attempt-1",
    update_idx: int = 1,
    observation_kind: Literal["SPSA", "LTC"] = "SPSA",
    result: GameResult = GameResult.BLACK_WIN,
) -> SpsaGameDatabaseRecord:
    identity = SpsaParticipationIdentity(
        run_id="run-1",
        update_idx=update_idx,
        pair_id=pair_id,
        attempt_id=attempt_id,
        observation_kind=observation_kind,
    )
    extra = identity_extra(identity)
    return SpsaGameDatabaseRecord(
        game_db_id=game_db_id,
        game_id=game_id,
        result=result,
        participation_extras=(extra, dict(extra)),
    )


def test_reconcile_reconstructs_missing_observation_and_is_idempotent() -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)
    source = _ObservationSource([_record()])

    assert ledger.reconcile(run_id="run-1", source=source) == 1
    assert ledger.reconcile(run_id="run-1", source=source) == 0
    row = connection.execute(
        "SELECT result_kind, game_db_id FROM game_observations WHERE game_id = 'game-1'"
    ).fetchone()
    assert row == ("BLACK_WIN", 1)


def test_reconcile_rejects_ledger_only_observation_without_mutation() -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)
    ledger.insert(build_ledger_observation(_record()))
    connection.commit()

    with pytest.raises(SpsaObservationIntegrityError, match="Ledger-only"):
        ledger.reconcile(run_id="run-1", source=_ObservationSource([]))

    assert connection.execute("SELECT count(*) FROM game_observations").fetchone() == (1,)


def test_reconcile_distinguishes_duplicate_attempt_from_duplicate_game() -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)
    source = _ObservationSource(
        [
            _record(),
            _record(game_db_id=2, game_id="game-2", attempt_id="attempt-1"),
        ]
    )

    with pytest.raises(SpsaObservationIntegrityError, match="Duplicate SPSA attempt"):
        ledger.reconcile(run_id="run-1", source=source)

    assert connection.execute("SELECT count(*) FROM game_observations").fetchone() == (0,)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (GameResult.WHITE_WIN, "WHITE_WIN"),
        (GameResult.DRAW_BY_REPETITION, "DRAW"),
        (GameResult.ERROR, "FAILED_OBSERVATION"),
        (GameResult.INVALID, "FAILED_OBSERVATION"),
        (GameResult.PAUSED, "INCOMPLETE"),
    ],
)
def test_result_mapping_never_coerces_non_game_outcome_to_draw(
    result: GameResult,
    expected: str,
) -> None:
    assert build_ledger_observation(_record(result=result)).result_kind == expected


def test_conflicting_game_evidence_is_rejected() -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)
    ledger.insert(build_ledger_observation(_record()))
    connection.commit()
    source = _ObservationSource([_record(result=GameResult.WHITE_WIN)])

    with pytest.raises(SpsaObservationIntegrityError, match="evidence conflict"):
        ledger.reconcile(run_id="run-1", source=source)


def test_reconcile_revalidates_existing_observation_assignment_kind() -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)
    record = _record()
    ledger.insert(build_ledger_observation(record))
    connection.execute("UPDATE pair_assignments SET assignment_kind = 'LTC' WHERE pair_id = 'pair-1'")
    connection.commit()

    with pytest.raises(SpsaObservationIntegrityError, match="update/kind"):
        ledger.reconcile(run_id="run-1", source=_ObservationSource([record]))

    assert connection.execute("SELECT observation_kind FROM game_observations WHERE game_id = 'game-1'").fetchone() == (
        "SPSA",
    )


def test_missing_or_mismatched_side_identity_is_rejected() -> None:
    missing = _record()
    missing = SpsaGameDatabaseRecord(
        game_db_id=missing.game_db_id,
        game_id=missing.game_id,
        result=missing.result,
        participation_extras=(missing.participation_extras[0],),
    )
    with pytest.raises(ValueError, match="exactly two"):
        build_ledger_observation(missing)

    other = _record(pair_id="pair-2").participation_extras[0]
    mismatch = _record()
    mismatch = SpsaGameDatabaseRecord(
        game_db_id=mismatch.game_db_id,
        game_id=mismatch.game_id,
        result=mismatch.result,
        participation_extras=(mismatch.participation_extras[0], other),
    )
    with pytest.raises(ValueError, match="do not match"):
        build_ledger_observation(mismatch)


@pytest.mark.parametrize(
    "record",
    [
        _record(update_idx=2),
        _record(observation_kind="LTC"),
    ],
)
def test_observation_update_and_kind_must_match_pair_assignment(
    record: SpsaGameDatabaseRecord,
) -> None:
    connection = _open_ledger()
    ledger = SpsaObservationLedger(connection)

    with pytest.raises(SpsaObservationIntegrityError, match="update/kind"):
        ledger.insert_committed(build_ledger_observation(record))

    assert connection.execute("SELECT count(*) FROM game_observations").fetchone() == (0,)
