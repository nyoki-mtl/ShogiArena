from __future__ import annotations

import json
import math
import random
import sqlite3

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import SprtConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import (
    LtcPassCriteria,
    LtcRegressionConfig,
)
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import (
    SpsaLedgerRuntime,
    SpsaLedgerStateError,
)
from shogiarena._core.contexts.spsa.adapters.ledger_schema import create_canonical_schema
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression import run_ltc_regression
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerPairAssignment, SpsaUpdateState
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256


def _runtime(*, contract: dict[str, object] | None = None) -> tuple[sqlite3.Connection, SpsaLedgerRuntime]:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    create_canonical_schema(connection)
    runtime = SpsaLedgerRuntime(connection, run_id="run-1")
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed="seed",
        contract=contract or _contract(),
        params=[_param()],
    )
    return connection, runtime


def _contract(
    *,
    sprt: dict[str, object] | None = None,
    min_winrate: float | None = 0.8,
    max_elo_drop: float | None = None,
) -> dict[str, object]:
    return {
        "name": "contract",
        "config": {
            "ltc_regression": {
                "pass_criteria": {
                    "min_winrate": min_winrate,
                    "max_elo_drop": max_elo_drop,
                    "sprt": sprt,
                }
            }
        },
    }


def _param() -> ParamEntry:
    return ParamEntry(
        name="Threads",
        type="int",
        value=1.0,
        min=1.0,
        max=8.0,
        step=1.0,
        delta=1.0,
        comment="",
        is_not_used=False,
    )


def _plan_and_assign(runtime: SpsaLedgerRuntime) -> None:
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",)),
        ltc_required=False,
    )
    _assign_spsa(runtime)


def _assign_spsa(runtime: SpsaLedgerRuntime) -> None:
    runtime.assign_pair(
        update_idx=1,
        pair_id="pair-1",
        assignment_kind="SPSA",
        opening={"sfen": "startpos"},
        color_assignment={
            "games": [
                {"slot": "black", "tuned_as": "black", "game_id": "pair-1-black"},
                {"slot": "white", "tuned_as": "white", "game_id": "pair-1-white"},
            ]
        },
        flips={"Threads": 1},
        rounding_samples={"Threads": 2},
    )


def _schedule(
    *,
    spsa: tuple[str, ...] = (),
    ltc: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "k": 1,
        "expected_pair_ids": {
            "SPSA": list(spsa),
            "LTC": list(ltc),
        },
    }


def _ltc_assignment(pair_index: int = 0) -> LedgerPairAssignment:
    pair_id = f"ltc-u000001-p{pair_index:06d}"
    return LedgerPairAssignment(
        pair_id=pair_id,
        opening={"opening_idx": 0, "start_sfen": "startpos"},
        color_assignment={
            "games": [
                {"slot": "black", "tuned_as": "black", "game_id": f"{pair_id}-black"},
                {"slot": "white", "tuned_as": "white", "game_id": f"{pair_id}-white"},
            ]
        },
        flips={},
        rounding_samples={},
    )


def _record_ltc_observations(
    connection: sqlite3.Connection,
    *,
    pair_index: int = 0,
    is_passed: bool = False,
    is_all_loss: bool = False,
) -> None:
    pair_id = f"ltc-u000001-p{pair_index:06d}"
    if is_all_loss:
        results = ("WHITE_WIN", "BLACK_WIN")
    else:
        results = ("BLACK_WIN", "WHITE_WIN") if is_passed else ("BLACK_WIN", "DRAW")
    for game_idx, (slot, result) in enumerate(zip(("black", "white"), results, strict=True), start=1):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, ?, ?, 'LTC', ?, ?, ?, 'now')
            """,
            (
                f"{pair_id}-{slot}",
                pair_id,
                f"ltc-attempt-{pair_index}-{game_idx}",
                result,
                game_idx,
                f"ltc-digest-{game_idx}",
            ),
        )
    connection.commit()


def _record_spsa_observations(connection: sqlite3.Connection) -> None:
    for game_idx, game_id, result in (
        (1, "pair-1-black", "BLACK_WIN"),
        (2, "pair-1-white", "DRAW"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"spsa-attempt-{game_idx}",
                result,
                100 + game_idx,
                f"spsa-digest-{game_idx}",
            ),
        )
    connection.commit()


def _ltc_evidence(*, is_passed: bool = False) -> dict[str, object]:
    tuned_wins = 2 if is_passed else 1
    draws = 0 if is_passed else 1
    winrate = 1.0 if is_passed else 0.75
    return {
        "update_idx": 1,
        "status": "passed" if is_passed else "failed",
        "is_accepted": is_passed,
        "total_pairs": 1,
        "pairs_played": 1,
        "total_games": 2,
        "tuned_wins": tuned_wins,
        "baseline_wins": 0,
        "draws": draws,
        "winrate": winrate,
        "elo": None if is_passed else -400 * math.log10(1 / winrate - 1),
        "average_score": 1.0 if is_passed else 0.5,
        "fail_reasons": [] if is_passed else ["winrate 0.750 below threshold 0.800"],
        "sprt": None,
        "sprt_decision": None,
    }


def _prepare_two_pair_ltc(
    *,
    record_all: bool = False,
    contract: dict[str, object] | None = None,
    is_passed: bool = False,
    is_all_loss: bool = False,
) -> tuple[sqlite3.Connection, SpsaLedgerRuntime]:
    connection, runtime = _runtime(contract=contract)
    pair_ids = ("ltc-u000001-p000000", "ltc-u000001-p000001")
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=pair_ids),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.transition(update_idx=1, target=SpsaUpdateState.LTC_PENDING)
    runtime.assign_ltc_pairs_and_start(
        update_idx=1,
        assignments=[_ltc_assignment(0), _ltc_assignment(1)],
    )
    _record_ltc_observations(
        connection,
        pair_index=0,
        is_passed=is_passed,
        is_all_loss=is_all_loss,
    )
    if record_all:
        _record_ltc_observations(
            connection,
            pair_index=1,
            is_passed=is_passed,
            is_all_loss=is_all_loss,
        )
    return connection, runtime


def _early_ltc_evidence(*, sprt_decision: str = "accept_h0") -> dict[str, object]:
    return {
        "update_idx": 1,
        "status": "failed",
        "is_accepted": False,
        "total_pairs": 2,
        "pairs_played": 1,
        "total_games": 2,
        "tuned_wins": 1,
        "baseline_wins": 0,
        "draws": 1,
        "winrate": 0.75,
        "elo": -400 * math.log10(1 / 0.75 - 1),
        "average_score": 0.5,
        "fail_reasons": ["winrate 0.750 below threshold 0.800"],
        "sprt": {
            "llr": 999.0,
            "lower": -2.9444389791664403,
            "upper": 2.9444389791664403,
            "decision": sprt_decision,
            "games": 2,
            "wins": 1,
            "losses": 0,
            "draws": 1,
            "winrate": 0.75,
            "elo": -400 * math.log10(1 / 0.75 - 1),
        },
        "sprt_decision": sprt_decision,
    }


def _complete_two_pair_ltc_evidence() -> dict[str, object]:
    winrate = 0.75
    return {
        "update_idx": 1,
        "status": "failed",
        "is_accepted": False,
        "total_pairs": 2,
        "pairs_played": 2,
        "total_games": 4,
        "tuned_wins": 2,
        "baseline_wins": 0,
        "draws": 2,
        "winrate": winrate,
        "elo": -400 * math.log10(1 / winrate - 1),
        "average_score": 0.5,
        "fail_reasons": ["winrate 0.750 below threshold 0.800"],
        "sprt": None,
        "sprt_decision": None,
    }


def _all_loss_two_pair_ltc_evidence() -> dict[str, object]:
    return {
        "update_idx": 1,
        "status": "failed",
        "is_accepted": False,
        "total_pairs": 2,
        "pairs_played": 2,
        "total_games": 4,
        "tuned_wins": 0,
        "baseline_wins": 4,
        "draws": 0,
        "winrate": 0.0,
        "elo": None,
        "average_score": -1.0,
        "fail_reasons": ["elo -inf below allowed drop of 10.0"],
        "sprt": None,
        "sprt_decision": None,
    }


def _game_record(result: GameResult) -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "game_name": "test",
                "game_type": "spsa",
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "test", "game_type": "spsa"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result.name, "ply_count": 0},
        }
    )


class _BudgetExhaustionRunner:
    def __init__(self, runtime: SpsaLedgerRuntime) -> None:
        self._ltc_config = LtcRegressionConfig(
            enabled=True,
            every_n_updates=1,
            total_pairs=2,
            pass_criteria=LtcPassCriteria(
                min_winrate=0.8,
                sprt=SprtConfig(
                    model="gsprt-pentanomial-v1",
                    elo0=0.0,
                    elo1=400.0,
                    alpha=0.49,
                    beta=0.49,
                    min_games=100,
                ),
            ),
        )
        self._ltc_last_completed: int | None = None
        self._sfens = ["startpos"]
        self.num_workers = 1
        self._ledger_runtime = runtime
        self.records: list[JsonObject] = []

    def _append_spsa_event(self, _payload: JsonObject) -> None:
        return

    def _record_ltc_result(self, record: JsonObject) -> None:
        self.records.append(record)

    def _ltc_normalize_result_for_sprt(self, result: GameResult, is_tuned_as_black: bool) -> GameResult:
        return SpsaOrchestrator._ltc_normalize_result_for_sprt(result, is_tuned_as_black)

    def _make_rng(self, **_kwargs: object) -> random.Random:
        return random.Random(0)

    async def _run_game_pair(self, *_args: object, **_kwargs: object) -> tuple[float, object, object]:
        return (
            1.0,
            _game_record(GameResult.BLACK_WIN),
            _game_record(GameResult.WHITE_WIN),
        )


def test_run_contract_is_idempotent_and_conflict_is_rejected() -> None:
    connection, runtime = _runtime()
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed="seed",
        contract=_contract(),
        params=[_param()],
    )

    with pytest.raises(SpsaLedgerStateError, match="run contract conflict"):
        runtime.initialize_run(
            resume_hash="different",
            space_digest="space",
            sealed_run_seed="seed",
            contract=_contract(),
            params=[_param()],
        )


def test_update_one_requires_sealed_initial_theta() -> None:
    connection, runtime = _runtime()

    with pytest.raises(SpsaLedgerStateError, match="sealed initial baseline"):
        runtime.plan_update(
            update_idx=1,
            theta_before={"Threads": 2.0},
            schedule=_schedule(spsa=("pair-1",)),
            ltc_required=False,
        )

    assert connection.execute("SELECT count(*) FROM updates").fetchone() == (0,)


def test_resume_revalidates_initial_theta_chain_link() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",)),
        ltc_required=False,
    )
    connection.execute(
        "UPDATE updates SET theta_before_json = '{\"Threads\":2.0}' WHERE run_id = 'run-1' AND update_idx = 1"
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="theta chain conflicts"):
        runtime.validate_resume_authority(
            resume_hash="resume",
            space_digest="space",
            params=[_param()],
        )


def test_existing_run_rejects_unbacked_or_corrupt_accepted_baseline() -> None:
    connection, runtime = _runtime()
    connection.execute(
        """
        UPDATE accepted_baseline
        SET accepted_update_idx = 1, theta_json = '{"Threads":2.0}'
        WHERE run_id = 'run-1'
        """
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="accepted baseline"):
        runtime.initialize_run(
            resume_hash="resume",
            space_digest="space",
            sealed_run_seed="seed",
            contract=_contract(),
            params=[_param()],
        )


def test_resume_rejects_committed_update_outside_contiguous_prefix() -> None:
    connection, runtime = _runtime()
    connection.execute(
        """
        INSERT INTO updates (
            run_id, update_idx, state, theta_before_json, theta_candidate_json,
            theta_final_json, schedule_json, ltc_required, revision, created_at, updated_at
        ) VALUES (
            'run-1', 2, 'COMMITTED', '{"Threads":1.0}', '{"Threads":2.0}',
            '{"Threads":2.0}', '{}', 0, 1, 'now', 'now'
        )
        """
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="theta chain has a gap before update 2"):
        runtime.validate_resume_authority(
            resume_hash="resume",
            space_digest="space",
            params=[_param()],
        )


def test_pair_assignment_is_durable_before_games_running() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)

    assert runtime.update_state(1) == SpsaUpdateState.GAMES_RUNNING
    assert connection.execute("SELECT pair_id FROM pair_assignments").fetchone() == ("pair-1",)


def test_games_cannot_start_with_undersized_spsa_assignment_set() -> None:
    _connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1", "pair-2")),
        ltc_required=False,
    )
    _assign_spsa(runtime)

    with pytest.raises(SpsaLedgerStateError, match="assignment set is incomplete"):
        runtime.mark_games_running(update_idx=1)

    assert runtime.update_state(1) == SpsaUpdateState.PLANNED


def test_candidate_cannot_advance_until_two_valid_pair_observations_exist() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)

    with pytest.raises(SpsaLedgerStateError, match="no valid assigned observation"):
        runtime.mark_games_complete(update_idx=1)

    for game_idx, game_id, result in (
        (1, "pair-1-black", "BLACK_WIN"),
        (2, "pair-1-white", "DRAW"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()
    runtime.mark_games_complete(update_idx=1)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})

    assert runtime.completed_updates() == 1
    assert runtime.current_theta() == {"Threads": 2.0}


def test_non_ltc_commit_requires_durable_candidate_theta() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",)),
        ltc_required=False,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )

    with pytest.raises(SpsaLedgerStateError, match="durable candidate"):
        runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 3.0})

    assert runtime.update_state(1) == SpsaUpdateState.CANDIDATE_COMPUTED


def test_non_game_observation_blocks_update_commit() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    for game_idx, game_id, result in (
        (1, "pair-1-black", "BLACK_WIN"),
        (2, "pair-1-white", "FAILED_OBSERVATION"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="no valid bounded retry"):
        runtime.mark_games_complete(update_idx=1)


def test_single_failed_observation_then_retry_can_complete_update() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.mark_games_running(update_idx=1)
    for game_idx, game_id, result in (
        (1, "pair-1-black", "BLACK_WIN"),
        (2, "pair-1-white", "FAILED_OBSERVATION"),
        (3, "pair-1-white-retry1", "DRAW"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()

    runtime.mark_games_complete(update_idx=1)

    assert runtime.update_state(1) == SpsaUpdateState.GAMES_COMPLETE


@pytest.mark.parametrize(
    ("observations", "message"),
    [
        (
            (
                ("pair-1-black", "BLACK_WIN"),
                ("pair-1-white", "DRAW"),
                ("unsealed-game", "DRAW"),
            ),
            "unexpected observation identities",
        ),
        (
            (
                ("pair-1-black", "BLACK_WIN"),
                ("pair-1-black-retry1", "DRAW"),
                ("pair-1-white", "DRAW"),
            ),
            "retries a valid observation",
        ),
        (
            (
                ("pair-1-black", "FAILED_OBSERVATION"),
                ("pair-1-black-retry2", "BLACK_WIN"),
                ("pair-1-white", "DRAW"),
            ),
            "unexpected observation identities",
        ),
    ],
)
def test_update_rejects_observations_outside_sealed_bounded_retry_identity(
    observations: tuple[tuple[str, str], ...],
    message: str,
) -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.mark_games_running(update_idx=1)
    for game_idx, (game_id, result) in enumerate(observations, start=1):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match=message):
        runtime.mark_games_complete(update_idx=1)


def test_variant_quarantine_event_is_durable_and_idempotent() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)

    kwargs = {
        "update_idx": 1,
        "pair_id": "pair-1",
        "variant_id": "v000001+",
        "failure_classification": "SpsaObservationError",
    }
    runtime.record_variant_quarantine(**kwargs)
    runtime.record_variant_quarantine(**kwargs)

    rows = connection.execute(
        """
        SELECT payload_json FROM event_revisions
        WHERE run_id = 'run-1' AND event_type = 'variant_quarantined'
        """
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(str(rows[0][0])) == {**kwargs, "retry_count": 1}


def test_ltc_authority_uses_valid_retry_for_failed_assigned_game() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=("ltc-u000001-p000000",)),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.mark_games_running(update_idx=1)
    runtime.mark_games_complete(update_idx=1)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.prepare_ltc(update_idx=1)
    runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])
    pair_id = "ltc-u000001-p000000"
    for game_idx, game_id, result in (
        (1, f"{pair_id}-black", "FAILED_OBSERVATION"),
        (2, f"{pair_id}-black-retry1", "BLACK_WIN"),
        (3, f"{pair_id}-white", "WHITE_WIN"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, ?, ?, 'LTC', ?, ?, ?, 'now')
            """,
            (
                game_id,
                pair_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()

    assert runtime._durable_ltc_pair_scores(update_idx=1, pair_id=pair_id) == (1.0, 1.0)


def test_ltc_authority_rejects_arbitrary_retry_suffix() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=("ltc-u000001-p000000",)),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.mark_games_running(update_idx=1)
    runtime.mark_games_complete(update_idx=1)
    runtime.store_candidate(update_idx=1, theta_candidate={"Threads": 2.0}, schedule={"score": 1})
    runtime.prepare_ltc(update_idx=1)
    runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])
    pair_id = "ltc-u000001-p000000"
    for game_idx, game_id, result in (
        (1, f"{pair_id}-black", "FAILED_OBSERVATION"),
        (2, f"{pair_id}-black-retry2", "BLACK_WIN"),
        (3, f"{pair_id}-white", "WHITE_WIN"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, ?, ?, 'LTC', ?, ?, ?, 'now')
            """,
            (game_id, pair_id, f"attempt-{game_idx}", result, game_idx, f"digest-{game_idx}"),
        )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="unexpected observation identities"):
        runtime._durable_ltc_pair_scores(update_idx=1, pair_id=pair_id)


def test_invalid_or_skipped_transition_is_rejected() -> None:
    _connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",)),
        ltc_required=False,
    )

    with pytest.raises(SpsaLedgerStateError, match="Invalid SPSA update transition"):
        runtime.transition(update_idx=1, target=SpsaUpdateState.CANDIDATE_COMPUTED)


@pytest.mark.parametrize("is_passed", [True, False])
def test_ltc_decision_commits_final_theta_baseline_and_revision_atomically(
    is_passed: bool,
) -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=("ltc-u000001-p000000",)),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.transition(update_idx=1, target=SpsaUpdateState.LTC_PENDING)
    runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])
    _record_ltc_observations(connection, is_passed=is_passed)

    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=is_passed,
        evidence=_ltc_evidence(is_passed=is_passed),
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )

    assert runtime.update_state(1) == SpsaUpdateState.COMMITTED
    assert runtime.current_theta() == {"Threads": 2.0 if is_passed else 1.0}
    baseline = connection.execute(
        "SELECT accepted_update_idx, theta_json FROM accepted_baseline WHERE run_id = 'run-1'"
    ).fetchone()
    assert baseline == (
        1 if is_passed else 0,
        '{"Threads":2.0}' if is_passed else '{"Threads":1.0}',
    )
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM event_revisions").fetchone() == (1,)
    assert runtime.latest_accepted_update_idx() == (1 if is_passed else None)
    if is_passed:
        accepted_best = runtime.accepted_best_commit(update_idx=1)
        assert accepted_best["acceptance"] == "ltc_pass"
        assert accepted_best["ltc_decision"] == {
            "tested_update_idx": 1,
            "baseline_update_idx": 0,
            "decision": "pass",
            "evidence_digest": canonical_sha256(_ltc_evidence(is_passed=True)),
            "revision": 1,
        }
    else:
        with pytest.raises(SpsaLedgerStateError, match="no accepted LTC decision"):
            runtime.accepted_best_commit(update_idx=1)


@pytest.mark.parametrize("sprt_decision", ["continue", "forged_terminal"])
def test_ltc_early_termination_requires_canonical_terminal_sprt_evidence(
    sprt_decision: str,
) -> None:
    connection, runtime = _prepare_two_pair_ltc()

    with pytest.raises(
        SpsaLedgerStateError,
        match="SPRT payload conflicts|terminal SPRT decision|invalid SPRT decision",
    ):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=0,
            is_passed=False,
            evidence=_early_ltc_evidence(sprt_decision=sprt_decision),
            accepted_theta={"Threads": 2.0},
            reverted_theta={"Threads": 1.0},
        )

    assert runtime.update_state(1) == SpsaUpdateState.LTC_RUNNING
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (0,)


def test_ltc_terminal_decision_is_recomputed_from_sealed_sprt_and_durable_games() -> None:
    connection, runtime = _prepare_two_pair_ltc(
        contract=_contract(
            sprt={
                "model": "gsprt-trinomial-v1",
                "elo0": 0.0,
                "elo1": 5.0,
                "alpha": 0.05,
                "beta": 0.05,
                "min_games": 100,
            }
        )
    )
    forged_evidence = _early_ltc_evidence(sprt_decision="accept_h1")

    with pytest.raises(SpsaLedgerStateError, match="SPRT payload conflicts"):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=0,
            is_passed=False,
            evidence=forged_evidence,
            accepted_theta={"Threads": 2.0},
            reverted_theta={"Threads": 1.0},
        )

    assert runtime.update_state(1) == SpsaUpdateState.LTC_RUNNING
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (0,)


@pytest.mark.asyncio
async def test_ltc_budget_exhaustion_commits_durable_fail_and_reverts_baseline() -> None:
    sprt_config = {
        "model": "gsprt-pentanomial-v1",
        "elo0": 0.0,
        "elo1": 400.0,
        "alpha": 0.49,
        "beta": 0.49,
        "min_games": 100,
    }
    connection, runtime = _prepare_two_pair_ltc(
        record_all=True,
        contract=_contract(sprt=sprt_config),
        is_passed=True,
    )
    runner = _BudgetExhaustionRunner(runtime)

    evidence = await run_ltc_regression(
        runner,
        update_idx=1,
        tuned_params=[_param()],
        baseline_params=[_param()],
        baseline_update_idx=0,
    )
    assert evidence["status"] == "failed"
    assert "sealed LTC pair budget exhausted without terminal SPRT decision" in evidence["fail_reasons"]

    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=False,
        evidence=evidence,
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )

    assert runtime.update_state(1) == SpsaUpdateState.COMMITTED
    assert runtime.current_theta() == {"Threads": 1.0}
    assert runtime.accepted_baseline() == (0, {"Threads": 1.0})
    assert connection.execute(
        "SELECT decision FROM ltc_decisions WHERE run_id = 'run-1' AND tested_update_idx = 1"
    ).fetchone() == ("fail",)


def test_next_update_cannot_be_planned_past_unresolved_ltc() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)

    with pytest.raises(SpsaLedgerStateError, match="before update 1 is committed"):
        runtime.plan_update(
            update_idx=2,
            theta_before={"Threads": 2.0},
            schedule=_schedule(spsa=("spsa-u000002-p000000",)),
            ltc_required=False,
        )

    assert connection.execute("SELECT count(*) FROM updates WHERE update_idx = 2").fetchone() == (0,)


def test_next_update_theta_must_match_committed_predecessor() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})

    accepted_best = runtime.accepted_best_commit(update_idx=1)
    assert accepted_best["acceptance"] == "without_ltc"
    assert accepted_best["parameters"] == [{"parameter_id": "Threads", "option_name": "Threads", "value": 2.0}]
    assert accepted_best["ltc_decision"] is None
    assert len(str(accepted_best["commit_id"])) == 64
    assert runtime.latest_accepted_update_idx() == 1

    with pytest.raises(SpsaLedgerStateError, match="theta does not match committed update 1"):
        runtime.plan_update(
            update_idx=2,
            theta_before={"Threads": 3.0},
            schedule=_schedule(spsa=("spsa-u000002-p000000",)),
            ltc_required=False,
        )

    assert connection.execute("SELECT count(*) FROM updates WHERE update_idx = 2").fetchone() == (0,)


def test_ltc_decision_rejects_forged_outcomes_against_durable_games() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)
    forged_evidence = _complete_two_pair_ltc_evidence()
    forged_evidence.update(
        {
            "status": "passed",
            "is_accepted": True,
            "tuned_wins": 4,
            "draws": 0,
            "winrate": 1.0,
            "elo": None,
            "fail_reasons": [],
        }
    )

    with pytest.raises(SpsaLedgerStateError, match="tuned_wins conflicts with durable observations"):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=0,
            is_passed=True,
            evidence=forged_evidence,
            accepted_theta={"Threads": 2.0},
            reverted_theta={"Threads": 1.0},
        )

    assert runtime.update_state(1) == SpsaUpdateState.LTC_RUNNING
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (0,)


def test_ltc_decision_rejects_forged_average_score() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)
    forged_evidence = _complete_two_pair_ltc_evidence()
    forged_evidence["average_score"] = -0.25

    with pytest.raises(SpsaLedgerStateError, match="average_score conflicts with durable observations"):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=0,
            is_passed=False,
            evidence=forged_evidence,
            accepted_theta={"Threads": 2.0},
            reverted_theta={"Threads": 1.0},
        )

    assert runtime.update_state(1) == SpsaUpdateState.LTC_RUNNING
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (0,)


def test_ltc_all_loss_fails_sealed_max_elo_drop() -> None:
    connection, runtime = _prepare_two_pair_ltc(
        record_all=True,
        contract=_contract(min_winrate=None, max_elo_drop=10.0),
        is_all_loss=True,
    )

    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=False,
        evidence=_all_loss_two_pair_ltc_evidence(),
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )

    assert runtime.update_state(1) == SpsaUpdateState.COMMITTED
    assert runtime.current_theta() == {"Threads": 1.0}
    decision = connection.execute(
        "SELECT decision FROM ltc_decisions WHERE run_id = 'run-1' AND tested_update_idx = 1"
    ).fetchone()
    assert decision == ("fail",)


def test_resume_revalidates_canonical_ltc_decision_evidence() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)
    evidence = _complete_two_pair_ltc_evidence()
    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=False,
        evidence=evidence,
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )
    runtime.validate_resume_authority(
        resume_hash="resume",
        space_digest="space",
        params=[_param()],
    )

    forged_evidence = dict(evidence)
    forged_evidence["status"] = "passed"
    forged_evidence["is_accepted"] = True
    connection.execute(
        """
        UPDATE ltc_decisions
        SET evidence_digest = ?, evidence_json = ?
        WHERE run_id = 'run-1' AND tested_update_idx = 1
        """,
        (
            canonical_sha256(forged_evidence),
            json.dumps(forged_evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="status conflicts"):
        runtime.validate_resume_authority(
            resume_hash="resume",
            space_digest="space",
            params=[_param()],
        )


def test_resume_rejects_ltc_outcome_corruption_against_committed_evidence() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)
    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=False,
        evidence=_complete_two_pair_ltc_evidence(),
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )
    connection.execute(
        """
        UPDATE game_observations
        SET result_kind = 'WHITE_WIN'
        WHERE run_id = 'run-1' AND game_id = 'ltc-u000001-p000000-white'
        """
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="tuned_wins conflicts with durable observations"):
        runtime.validate_resume_authority(
            resume_hash="resume",
            space_digest="space",
            params=[_param()],
        )


def test_resume_rejects_post_commit_average_score_corruption() -> None:
    connection, runtime = _prepare_two_pair_ltc(record_all=True)
    evidence = _complete_two_pair_ltc_evidence()
    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=False,
        evidence=evidence,
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )
    forged_evidence = dict(evidence)
    forged_evidence["average_score"] = -0.25
    connection.execute(
        """
        UPDATE ltc_decisions
        SET evidence_digest = ?, evidence_json = ?
        WHERE run_id = 'run-1' AND tested_update_idx = 1
        """,
        (
            canonical_sha256(forged_evidence),
            json.dumps(forged_evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="average_score conflicts with durable observations"):
        runtime.validate_resume_authority(
            resume_hash="resume",
            space_digest="space",
            params=[_param()],
        )


@pytest.mark.parametrize(
    ("baseline_update_idx", "reverted_theta", "message"),
    [
        (1, {"Threads": 1.0}, "baseline index"),
        (0, {"Threads": 3.0}, "revert theta"),
    ],
)
def test_ltc_decision_requires_current_accepted_baseline(
    baseline_update_idx: int,
    reverted_theta: dict[str, float],
    message: str,
) -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=("ltc-u000001-p000000",)),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.prepare_ltc(update_idx=1)
    runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])
    _record_ltc_observations(connection)

    with pytest.raises(SpsaLedgerStateError, match=message):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=baseline_update_idx,
            is_passed=False,
            evidence=_ltc_evidence(),
            accepted_theta={"Threads": 2.0},
            reverted_theta=reverted_theta,
        )


def test_ltc_pass_requires_durable_candidate_theta() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",), ltc=("ltc-u000001-p000000",)),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    _record_spsa_observations(connection)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.prepare_ltc(update_idx=1)
    runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])
    _record_ltc_observations(connection, is_passed=True)

    with pytest.raises(SpsaLedgerStateError, match="durable candidate"):
        runtime.commit_ltc_decision(
            update_idx=1,
            baseline_update_idx=0,
            is_passed=True,
            evidence=_ltc_evidence(is_passed=True),
            accepted_theta={"Threads": 3.0},
            reverted_theta={"Threads": 1.0},
        )

    assert runtime.update_state(1) == SpsaUpdateState.LTC_RUNNING
    assert connection.execute("SELECT count(*) FROM ltc_decisions").fetchone() == (0,)


def test_ltc_cannot_start_with_undersized_assignment_set() -> None:
    connection, runtime = _runtime()
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(
            spsa=("pair-1",),
            ltc=("ltc-u000001-p000000", "ltc-u000001-p000001"),
        ),
        ltc_required=True,
    )
    _assign_spsa(runtime)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    runtime.prepare_ltc(update_idx=1)

    with pytest.raises(SpsaLedgerStateError, match="sealed pair set"):
        runtime.assign_ltc_pairs_and_start(update_idx=1, assignments=[_ltc_assignment()])

    assert runtime.update_state(1) == SpsaUpdateState.LTC_PENDING
    assert connection.execute("SELECT count(*) FROM pair_assignments WHERE assignment_kind = 'LTC'").fetchone() == (0,)


@pytest.mark.parametrize(
    ("accepted_update_idx", "theta_json", "evidence_digest", "revision"),
    [
        (1, '{"Threads":2.0}', "evidence-1", 1),
        (2, '{"Threads":3.0}', "evidence-2", 1),
    ],
)
def test_resume_rejects_stale_or_revision_mismatched_accepted_baseline(
    accepted_update_idx: int,
    theta_json: str,
    evidence_digest: str,
    revision: int,
) -> None:
    connection, runtime = _runtime()
    for update_idx, before, final in ((1, 1.0, 2.0), (2, 2.0, 3.0)):
        connection.execute(
            """
            INSERT INTO updates (
                run_id, update_idx, state, theta_before_json, theta_candidate_json,
                theta_final_json, schedule_json, ltc_required, revision, created_at, updated_at
            ) VALUES ('run-1', ?, 'COMMITTED', ?, ?, ?, '{}', 1, 3, 'now', 'now')
            """,
            (
                update_idx,
                f'{{"Threads":{before}}}',
                f'{{"Threads":{final}}}',
                f'{{"Threads":{final}}}',
            ),
        )
        connection.execute(
            """
            INSERT INTO ltc_decisions (
                run_id, tested_update_idx, baseline_update_idx, decision,
                evidence_digest, evidence_json, final_theta_json, decided_at, revision
            ) VALUES ('run-1', ?, ?, 'pass', ?, '{}', ?, 'now', ?)
            """,
            (
                update_idx,
                update_idx - 1,
                f"evidence-{update_idx}",
                f'{{"Threads":{final}}}',
                update_idx,
            ),
        )
    connection.execute(
        """
        UPDATE accepted_baseline
        SET accepted_update_idx = ?, theta_json = ?, evidence_digest = ?, revision = ?
        WHERE run_id = 'run-1'
        """,
        (accepted_update_idx, theta_json, evidence_digest, revision),
    )
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="latest committed passing LTC decision"):
        runtime.initialize_run(
            resume_hash="resume",
            space_digest="space",
            sealed_run_seed="seed",
            contract=_contract(),
            params=[_param()],
        )


def test_running_update_replay_validates_assignment_and_restores_next_action() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.mark_games_running(update_idx=1)
    for game_idx, game_id, result in (
        (1, "pair-1-black", "BLACK_WIN"),
        (2, "pair-1-white", "DRAW"),
    ):
        connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                game_db_id, evidence_digest, observed_at
            ) VALUES ('run-1', ?, 1, 'pair-1', ?, 'SPSA', ?, ?, ?, 'now')
            """,
            (
                game_id,
                f"attempt-{game_idx}",
                result,
                game_idx,
                f"digest-{game_idx}",
            ),
        )
    connection.commit()

    resumed = SpsaLedgerRuntime(connection, run_id="run-1")
    resumed.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule=_schedule(spsa=("pair-1",)),
        ltc_required=False,
    )
    resumed.assign_pair(
        update_idx=1,
        pair_id="pair-1",
        assignment_kind="SPSA",
        opening={"sfen": "startpos"},
        color_assignment={
            "games": [
                {"slot": "black", "tuned_as": "black", "game_id": "pair-1-black"},
                {"slot": "white", "tuned_as": "white", "game_id": "pair-1-white"},
            ]
        },
        flips={"Threads": 1},
        rounding_samples={"Threads": 2},
    )
    assert resumed.game_result_kind(game_id="pair-1-black") == "BLACK_WIN"
    resumed.mark_games_running(update_idx=1)
    resumed.mark_games_complete(update_idx=1)
    resumed.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score": 1},
    )
    resumed.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})

    assert resumed.completed_updates() == 1
    assert resumed.current_theta() == {"Threads": 2.0}


def test_assignment_rederivation_conflict_does_not_mutate_existing_row() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    before = connection.execute(
        "SELECT assignment_digest, opening_json FROM pair_assignments WHERE pair_id = 'pair-1'"
    ).fetchone()

    with pytest.raises(SpsaLedgerStateError, match="assignment conflict"):
        runtime.assign_pair(
            update_idx=1,
            pair_id="pair-1",
            assignment_kind="SPSA",
            opening={"sfen": "different"},
            color_assignment={"games": ["black", "white"]},
            flips={"Threads": 1},
            rounding_samples={"Threads": 2},
        )

    after = connection.execute(
        "SELECT assignment_digest, opening_json FROM pair_assignments WHERE pair_id = 'pair-1'"
    ).fetchone()
    assert after == before


def test_cancelled_terminal_is_resumable_and_invalidated_before_dispatch() -> None:
    connection, runtime = _runtime()
    _plan_and_assign(runtime)
    runtime.mark_games_running(update_idx=1)
    runtime.commit_terminal(
        status="with-anomalies",
        reason="cancelled_resumable",
        resumable=True,
    )

    assert runtime.assert_resume_allowed() is True
    payload = runtime.terminal_payload()
    assert payload is not None
    assert payload["last_committed_update"] == 0
    assert payload["pending_stage"] == "GAMES_RUNNING"
    completion = runtime.completion_status_payload()
    assert completion is not None
    assert completion["status"] == "with-anomalies"
    assert completion["termination_reason"] == "cancelled_resumable"
    assert completion["last_committed_update"] == 0
    assert completion["pending_update"] == 1
    assert completion["pending_stage"] == "GAMES_RUNNING"
    assert completion["manifest"] == {
        "resume_hash": "resume",
        "space_digest": "space",
    }
    runtime.invalidate_resumable_terminal()

    assert runtime.terminal_payload() is None
    assert connection.execute("SELECT status FROM run_contract WHERE run_id = 'run-1'").fetchone() == ("running",)


@pytest.mark.parametrize("companion", ["run_contract", "event"])
def test_terminal_replay_rejects_inconsistent_companion_record(companion: str) -> None:
    connection, runtime = _runtime()
    runtime.commit_terminal(status="clean", reason="completed", resumable=False)
    if companion == "run_contract":
        connection.execute("UPDATE run_contract SET status = 'running' WHERE run_id = 'run-1'")
    else:
        connection.execute("DELETE FROM event_revisions WHERE run_id = 'run-1'")
    connection.commit()

    with pytest.raises(SpsaLedgerStateError, match="terminal companion records"):
        runtime.commit_terminal(status="clean", reason="completed", resumable=False)


@pytest.mark.parametrize("reason", ["completed", "early_stopped", "failed"])
def test_permanent_terminal_reason_rejects_resume(reason: str) -> None:
    _connection, runtime = _runtime()
    runtime.commit_terminal(
        status="failed" if reason == "failed" else "clean",
        reason=reason,
        resumable=False,
    )

    with pytest.raises(SpsaLedgerStateError, match=f"terminal \\({reason}\\)"):
        runtime.assert_resume_allowed()
