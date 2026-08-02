from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import shogiarena._core.contexts.spsa.adapters.runner as runner_module
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import RunOptions
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedger, open_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import (
    materialize_spsa_engine_configs,
    prepare_spsa_domain_inputs,
    prepare_spsa_run_directory,
)
from shogiarena._core.contexts.spsa.adapters.tunable_manifest_preflight import (
    TUNABLE_HANDSHAKE_FILENAME,
    TUNABLE_HANDSHAKE_SCHEMA,
)
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.application.space_spec import load_spsa_space_spec
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerPairAssignment
from shogiarena._core.contexts.spsa.domain.versioned_rng import HmacSha256Rng
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256
from tests.helpers.spsa_crash_worker import _record_authority_games

RUN_SEED = "01" * 32


class _Config(SimpleNamespace):
    def model_copy(self, *, deep: bool) -> _Config:
        assert deep is True
        return _Config(**copy.deepcopy(vars(self)))

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"experiment_name": "resume-test", "run_seed": RUN_SEED}


def _write_inputs(tmp_path: Path) -> _Config:
    space_path = tmp_path / "space.yaml"
    space_path.write_text(
        """
schema_version: shogiarena.spsa.space.v1
target:
  protocol: usi_options
parameters:
  - id: tempo
    target:
      option: TuneTempo
      value_encoding: decimal
    value_type: float
    initial: 10.0
    bounds:
      min: 0.0
      max: 20.0
    schedule:
      c_end: 2.0
      r_end: 0.5
""".lstrip(),
        encoding="utf-8",
    )
    sfens_path = tmp_path / "sfens.txt"
    sfens_path.write_text("startpos\n", encoding="utf-8")
    return _Config(
        experiment_name="resume-test",
        run_seed=RUN_SEED,
        space_path=space_path,
        start_sfens_path=sfens_path,
        baseline=[],
        tuned=[],
        num_updates=3,
        pairs_per_update=2,
        scale=1.0,
        algorithm_a=None,
        alpha=0.602,
        gamma=0.101,
        is_crn_enabled=True,
        int_rounding="none",
        int_ck_floor=0.0,
        is_snap_float_to_step=False,
        early_stop=None,
        inflight_factor=1,
        variants=SimpleNamespace(apply=SimpleNamespace(is_clear_hash_enabled=True)),
    )


def _prepare(
    tmp_path: Path,
    config: _Config,
    *,
    completed_updates: int | None = None,
    theta: dict[str, float] | None = None,
):
    result = prepare_spsa_domain_inputs(
        config=config,
        run_dir=tmp_path,
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
        authority_completed_updates=completed_updates,
        authority_theta=theta,
    )
    write_json_atomic(tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME, _tunable_evidence(config))
    return result


def _tunable_evidence(config: _Config) -> dict[str, object]:
    space = load_spsa_space_spec(config.space_path)
    advertised_options = [
        {
            "name": "TuneTempo",
            "type": "string",
            "default": "10",
            "minimum": None,
            "maximum": None,
            "choices": [],
        },
        {
            "name": "Clear Hash",
            "type": "button",
            "default": None,
            "minimum": None,
            "maximum": None,
            "choices": [],
        },
    ]
    return {
        "schema_version": TUNABLE_HANDSHAKE_SCHEMA,
        "status": "passed",
        "response_status": "timeout_optional",
        "command": "usi_tunables",
        "required": False,
        "runtime_scope": "local_runtime",
        "instance_id": None,
        "engine_name": "tuned",
        "manifest": None,
        "advertised_options": advertised_options,
        "clear_hash_engines": [
            {"role": "baseline", "index": 0, "advertised_options": advertised_options},
            {"role": "tuned", "index": 0, "advertised_options": advertised_options},
        ],
        "normalized_space": space.to_json(),
    }


def _create_ledger(tmp_path: Path) -> None:
    open_spsa_ledger(tmp_path).close()


def _runner(
    tmp_path: Path,
    config: _Config,
    *,
    storage: object | None = None,
) -> SpsaRunner:
    class _Metadata:
        def build_sealed_hashes(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                config_fingerprint="inputs-hash",
                schedule_hash="schedule-hash",
                resume_hash="resume-hash",
            )

        def seal_provenance_manifest(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(hashes=SimpleNamespace(schedule_hash="schedule-hash", resume_hash="resume-hash"))

    class _ReadOnlyDb:
        def ensure_schema(self) -> None: ...
        def close(self) -> None: ...
        def get_spsa_game_database_records(self, *, run_id: str) -> list[object]:
            del run_id
            return []

    if storage is None:

        def _read_json(path: str) -> dict[str, object]:
            if path == f"spsa/{TUNABLE_HANDSHAKE_FILENAME}":
                return _tunable_evidence(config)
            if path == "spsa/fixed_option_preflight.json":
                return {
                    "schema_version": "shogiarena.spsa.fixed_option_preflight.v2",
                    "status": "passed",
                    "target_option_names": ["TuneTempo"],
                    "engines": [],
                }
            return {
                "status": "provenance_sealed",
                "inputs_hash": "inputs-hash",
                "hashes": {
                    "schedule_hash": "schedule-hash",
                    "resume_hash": "resume-hash",
                },
            }

        storage = SimpleNamespace(
            read_json=_read_json,
            read_only_db_service=lambda: _ReadOnlyDb(),
        )
    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner.config = config
    runner._storage = storage
    runner._state = SpsaRunnerState()
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = _Metadata()
    runner._frozen_run_config_payload = {"experiment_name": "resume-test"}
    runner._engine_factory_service = SimpleNamespace(artifact_resolver=None)
    return runner


def _write_resume_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "status": "provenance_sealed",
                "inputs_hash": "inputs-hash",
                "hashes": {
                    "schedule_hash": "schedule-hash",
                    "resume_hash": "resume-hash",
                },
            }
        ),
        encoding="utf-8",
    )


def _initialize_resume_ledger(tmp_path: Path, config: _Config) -> tuple[SpsaLedger, SpsaLedgerRuntime]:
    space = load_spsa_space_spec(config.space_path)
    ledger = open_spsa_ledger(tmp_path)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="resume-test")
    space_digest = canonical_sha256(space.to_json())
    runtime.initialize_run(
        resume_hash="resume-hash",
        space_digest=space_digest,
        sealed_run_seed=RUN_SEED,
        contract={
            "resume_hash": "resume-hash",
            "space_digest": space_digest,
            "config": {"experiment_name": "resume-test", "run_seed": RUN_SEED},
        },
        params=space.to_param_entries(),
    )
    return ledger, runtime


def _tree_bytes(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_ledger_without_state_is_resume_candidate_before_manifest_write(tmp_path: Path) -> None:
    ledger_path = tmp_path / "spsa" / "ledger.sqlite3"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(b"ledger-authority")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b'{"status":"provenance_sealed"}')

    class _Metadata:
        def write_inputs_only_manifest(self, **_kwargs: object) -> object:
            raise AssertionError("resume candidate must not rewrite manifest.json")

    run_dir, frozen = prepare_spsa_run_directory(
        run_dir=tmp_path,
        should_skip_resume=False,
        config_payload={"experiment_name": "resume-test"},
        run_metadata_service=_Metadata(),  # type: ignore[arg-type]
        cleanup_run_dir=lambda *_args, **_kwargs: None,
    )

    assert run_dir == tmp_path
    assert frozen == {"experiment_name": "resume-test"}
    assert manifest_path.read_bytes() == b'{"status":"provenance_sealed"}'


def test_ledger_without_state_rebuilds_state_from_authority(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _create_ledger(tmp_path)
    (tmp_path / "state.json").unlink()

    _prepare(tmp_path, config, completed_updates=0, theta={"tempo": 10.0})

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["completed_updates"] == 0
    assert state["resume_hash"] == "resume-hash"


def test_ledger_without_state_restores_sealed_run_seed(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    (tmp_path / "state.json").unlink()

    resolved = runner_module._resolve_spsa_run_seed(
        configured_seed=None,
        run_dir=tmp_path,
        should_skip_resume=False,
        experiment_name="resume-test",
    )

    assert resolved == RUN_SEED


def test_read_only_db_snapshot_includes_live_wal_without_mutating_run_tree(tmp_path: Path) -> None:
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    _record_authority_games(database, pair_id="pair-1", kind="SPSA")
    assert (tmp_path / "game.db-wal").is_file()
    before = _tree_bytes(tmp_path)

    snapshot = storage.read_only_db_service()
    try:
        records = snapshot.get_spsa_game_database_records(run_id="run-1")
    finally:
        snapshot.close()

    assert len(records) == 2
    assert _tree_bytes(tmp_path) == before
    database.close()


def test_prepare_spsa_domain_inputs_restores_theta_and_skips_completed_updates(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    initial_params, _sfens, initial_items = _prepare(tmp_path, config)
    _create_ledger(tmp_path)
    assert initial_params[0].value == 10.0
    assert initial_items == [1, 2, 3]

    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["completed_updates"] = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")
    current_path = tmp_path / "spsa" / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "schema_version": "shogiarena.spsa.current.v1",
                "update_idx": 2,
                "pair_index_end": 4,
                "theta": {"tempo": 17.5},
            }
        ),
        encoding="utf-8",
    )
    restored_params, sfens, remaining_items = _prepare(
        tmp_path,
        config,
        completed_updates=2,
        theta={"tempo": 17.5},
    )

    assert restored_params[0].value == 17.5
    assert sfens == ["startpos"]
    assert remaining_items == [3]
    assert json.loads(current_path.read_text(encoding="utf-8"))["theta"] == {"tempo": 17.5}
    assert json.loads(state_path.read_text(encoding="utf-8"))["completed_updates"] == 2


@pytest.mark.asyncio
async def test_spsa_runner_prepare_domain_ignores_derived_json_when_ledger_is_empty(
    tmp_path: Path,
) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["completed_updates"] = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "spsa" / "current.json").write_text(
        json.dumps(
            {
                "schema_version": "shogiarena.spsa.current.v1",
                "update_idx": 2,
                "pair_index_end": 4,
                "theta": {"tempo": 17.5},
            }
        ),
        encoding="utf-8",
    )

    runner = _runner(tmp_path, config)

    await runner.prepare_domain()

    assert runner._state.params is not None
    assert runner._state.params[0].value == 10.0
    assert runner._state.update_items == [1, 2, 3]
    rebuilt = json.loads((tmp_path / "spsa" / "current.json").read_text(encoding="utf-8"))
    assert rebuilt["theta"] == {"tempo": 10.0}


@pytest.mark.asyncio
async def test_spsa_runner_resume_rejects_corrupt_normalized_space_before_writes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    normalized_path = tmp_path / "spsa" / "space.normalized.json"
    normalized_path.write_text("{}", encoding="utf-8")
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="normalized space artifact mismatch"):
        await _runner(tmp_path, config).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_legacy_json_only_resume_rejection_does_not_mutate_artifacts(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(ValueError, match="Legacy JSON-only"):
        await _runner(tmp_path, config).prepare_run_dir()

    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.asyncio
async def test_ledger_contract_conflict_does_not_rewrite_state_or_current(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    params, _sfens, _updates = _prepare(tmp_path, config)
    ledger = open_spsa_ledger(tmp_path)
    SpsaLedgerRuntime(ledger.connection, run_id="resume-test").initialize_run(
        resume_hash="different",
        space_digest="different",
        sealed_run_seed="different",
        contract={"config": {"experiment_name": "other"}},
        params=params,
    )
    ledger.close()
    state_path = tmp_path / "state.json"
    current_path = tmp_path / "spsa" / "current.json"
    before = (state_path.read_bytes(), current_path.read_bytes())

    with pytest.raises(RuntimeError, match="run contract conflict"):
        await _runner(tmp_path, config).prepare_domain()

    assert (state_path.read_bytes(), current_path.read_bytes()) == before


@pytest.mark.asyncio
async def test_production_resume_ledger_only_observation_keeps_tree_bytes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _write_resume_manifest(tmp_path)
    ledger, runtime = _initialize_resume_ledger(tmp_path, config)
    rng = HmacSha256Rng(
        seed_hex=RUN_SEED,
        domain="spsa.flip",
        run_id="resume-test",
        update_idx=1,
        parameter_id="tempo",
    )
    flip = 1 if rng.randint(0, 1) else -1
    runtime.plan_update(
        update_idx=1,
        theta_before={"tempo": 10.0},
        schedule={
            "k": 1,
            "c": {"tempo": 2.0},
            "flips": {"tempo": flip},
            "expected_pair_ids": {
                "SPSA": [
                    "spsa-u000001-p000000",
                    "spsa-u000001-p000001",
                ],
                "LTC": [],
            },
        },
        ltc_required=False,
    )
    runtime.assign_pair(
        update_idx=1,
        pair_id="spsa-u000001-p000000",
        assignment_kind="SPSA",
        opening={"opening_idx": 0, "start_sfen": "startpos", "batch_idx": 0},
        color_assignment={
            "games": [
                {
                    "slot": "black",
                    "tuned_as": "black",
                    "game_id": "spsa-u000001-p000000-black",
                },
                {
                    "slot": "white",
                    "tuned_as": "white",
                    "game_id": "spsa-u000001-p000000-white",
                },
            ]
        },
        flips={"tempo": flip},
        rounding_samples={
            "plus_options": {"TuneTempo": str(int(10 + 2 * flip))},
            "minus_options": {"TuneTempo": str(int(10 - 2 * flip))},
        },
    )
    ledger.connection.execute(
        """
        INSERT INTO game_observations (
            run_id, game_id, update_idx, pair_id, attempt_id, observation_kind,
            result_kind, game_db_id, evidence_digest, observed_at
        ) VALUES (
            'resume-test', 'game-1', 1, 'spsa-u000001-p000000', 'attempt-1', 'SPSA',
            'BLACK_WIN', 1, 'evidence', 'now'
        )
        """
    )
    ledger.connection.commit()
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="PLANNED state has game observations"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_production_resume_assignment_digest_conflict_keeps_tree_bytes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _write_resume_manifest(tmp_path)
    ledger, runtime = _initialize_resume_ledger(tmp_path, config)
    runtime.plan_update(
        update_idx=1,
        theta_before={"tempo": 10.0},
        schedule={"k": 1, "expected_pair_ids": {"SPSA": ["pair-1"], "LTC": []}},
        ltc_required=False,
    )
    runtime.assign_pair(
        update_idx=1,
        pair_id="pair-1",
        assignment_kind="SPSA",
        opening={"start_sfen": "startpos"},
        color_assignment={"games": ["black", "white"]},
        flips={"tempo": 1},
        rounding_samples={"tempo": 0.5},
    )
    ledger.connection.execute(
        "UPDATE pair_assignments SET opening_json = '{\"start_sfen\":\"corrupt\"}' WHERE pair_id = 'pair-1'"
    )
    ledger.connection.commit()
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="assignment digest conflict"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment_kind", ["SPSA", "LTC"])
async def test_production_resume_rejects_undersized_pair_set_without_writes(
    tmp_path: Path,
    assignment_kind: str,
) -> None:
    config = _write_inputs(tmp_path)
    if assignment_kind == "LTC":
        config.pairs_per_update = 1
        config.ltc_regression = SimpleNamespace(total_pairs=2)
    _prepare(tmp_path, config)
    _write_resume_manifest(tmp_path)
    ledger, runtime = _initialize_resume_ledger(tmp_path, config)
    expected_spsa = [f"spsa-u000001-p{pair_idx:06d}" for pair_idx in range(config.pairs_per_update)]
    expected_ltc = ["ltc-u000001-p000000", "ltc-u000001-p000001"] if assignment_kind == "LTC" else []
    runtime.plan_update(
        update_idx=1,
        theta_before={"tempo": 10.0},
        schedule={
            "c": {"tempo": 2.0},
            "flips": {"tempo": 1},
            "expected_pair_ids": {
                "SPSA": expected_spsa,
                "LTC": expected_ltc,
            },
        },
        ltc_required=assignment_kind == "LTC",
    )
    spsa_pair = expected_spsa[0]
    runtime.assign_pair(
        update_idx=1,
        pair_id=spsa_pair,
        assignment_kind="SPSA",
        opening={"start_sfen": "startpos"},
        color_assignment={
            "games": [
                {"slot": "black", "tuned_as": "black", "game_id": f"{spsa_pair}-1"},
                {"slot": "white", "tuned_as": "white", "game_id": f"{spsa_pair}-2"},
            ]
        },
        flips={"tempo": 1},
        rounding_samples={},
    )
    state = "GAMES_RUNNING"
    if assignment_kind == "LTC":
        for game_idx, result_kind in ((1, "BLACK_WIN"), (2, "DRAW")):
            ledger.connection.execute(
                """
                INSERT INTO game_observations (
                    run_id, game_id, update_idx, pair_id, attempt_id, observation_kind,
                    result_kind, game_db_id, evidence_digest, observed_at
                ) VALUES ('resume-test', ?, 1, ?, ?, 'SPSA', ?, ?, ?, 'now')
                """,
                (
                    f"{spsa_pair}-{game_idx}",
                    spsa_pair,
                    f"attempt-{game_idx}",
                    result_kind,
                    game_idx,
                    f"digest-{game_idx}",
                ),
            )
        ledger.connection.execute(
            "UPDATE updates SET state = 'LTC_PENDING' WHERE run_id = 'resume-test' AND update_idx = 1"
        )
        runtime.assign_ltc_pairs_and_start(
            update_idx=1,
            assignments=[
                LedgerPairAssignment(
                    pair_id=pair_id,
                    opening={"start_sfen": "startpos"},
                    color_assignment={
                        "games": [
                            {
                                "slot": "black",
                                "tuned_as": "black",
                                "game_id": f"{pair_id}-1",
                            },
                            {
                                "slot": "white",
                                "tuned_as": "white",
                                "game_id": f"{pair_id}-2",
                            },
                        ]
                    },
                    flips={},
                    rounding_samples={},
                )
                for pair_id in expected_ltc
            ],
        )
        ledger.connection.execute(
            "DELETE FROM pair_assignments WHERE run_id = 'resume-test' AND pair_id = ?",
            (expected_ltc[1],),
        )
        state = "LTC_RUNNING"
    ledger.connection.execute(
        "UPDATE updates SET state = ? WHERE run_id = 'resume-test' AND update_idx = 1",
        (state,),
    )
    ledger.connection.commit()
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="assignment set is incomplete"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_production_resume_rederives_self_consistent_assignment_before_writes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _write_resume_manifest(tmp_path)
    ledger, runtime = _initialize_resume_ledger(tmp_path, config)
    rng = HmacSha256Rng(
        seed_hex=RUN_SEED,
        domain="spsa.flip",
        run_id="resume-test",
        update_idx=1,
        parameter_id="tempo",
    )
    flip = 1 if rng.randint(0, 1) else -1
    schedule = {
        "c": {"tempo": 2.0},
        "flips": {"tempo": flip},
        "expected_pair_ids": {
            "SPSA": [
                "spsa-u000001-p000000",
                "spsa-u000001-p000001",
            ],
            "LTC": [],
        },
    }
    pair_id = "spsa-u000001-p000000"
    color_assignment = {
        "games": [
            {"slot": "black", "tuned_as": "black", "game_id": f"{pair_id}-black"},
            {"slot": "white", "tuned_as": "white", "game_id": f"{pair_id}-white"},
        ]
    }
    rounding_samples = {
        "plus_options": {"TuneTempo": str(int(10 + 2 * flip))},
        "minus_options": {"TuneTempo": str(int(10 - 2 * flip))},
    }
    runtime.plan_update(
        update_idx=1,
        theta_before={"tempo": 10.0},
        schedule=schedule,
        ltc_required=False,
    )
    runtime.assign_pair(
        update_idx=1,
        pair_id=pair_id,
        assignment_kind="SPSA",
        opening={"opening_idx": 0, "start_sfen": "startpos", "batch_idx": 0},
        color_assignment=color_assignment,
        flips={"tempo": flip},
        rounding_samples=rounding_samples,
    )
    corrupt_opening = {"opening_idx": 0, "start_sfen": "corrupt", "batch_idx": 0}
    corrupt_digest = canonical_sha256(
        {
            "run_id": "resume-test",
            "update_idx": 1,
            "pair_id": pair_id,
            "assignment_kind": "SPSA",
            "opening": corrupt_opening,
            "color_assignment": color_assignment,
            "flips": {"tempo": flip},
            "rounding_samples": rounding_samples,
        }
    )
    ledger.connection.execute(
        """
        UPDATE pair_assignments
        SET opening_json = ?, assignment_digest = ?
        WHERE run_id = 'resume-test' AND pair_id = ?
        """,
        (json.dumps(corrupt_opening, separators=(",", ":"), sort_keys=True), corrupt_digest, pair_id),
    )
    ledger.connection.commit()
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="assignment conflict"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("companion", ["run_contract", "event"])
async def test_production_resume_rejects_terminal_companion_corruption_before_writes(
    tmp_path: Path,
    companion: str,
) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _write_resume_manifest(tmp_path)
    ledger, runtime = _initialize_resume_ledger(tmp_path, config)
    runtime.commit_terminal(status="clean", reason="completed", resumable=False)
    if companion == "run_contract":
        ledger.connection.execute("UPDATE run_contract SET status = 'running' WHERE run_id = 'resume-test'")
    else:
        ledger.connection.execute("DELETE FROM event_revisions WHERE run_id = 'resume-test'")
    ledger.connection.commit()
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="terminal companion records"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_production_resume_rejects_external_engine_config_mutation_before_writes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    engine_binary.write_bytes(b"engine")
    engine_yaml = external_dir / "engine.yaml"
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\noptions:\n  Threads: 1\n",
        encoding="utf-8",
    )
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]
    _prepare(tmp_path, config)
    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)
    resume_engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [resume_engine]
    config.tuned = [resume_engine]
    _write_resume_manifest(tmp_path)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\noptions:\n  Threads: 2\n",
        encoding="utf-8",
    )
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="resolved engine provenance"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_production_resume_rejects_external_config_toctou_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    engine_binary.write_bytes(b"engine")
    engine_yaml = external_dir / "engine.yaml"
    original_yaml = f"engine_path: {engine_binary}\noptions:\n  Threads: 1\n"
    engine_yaml.write_text(original_yaml, encoding="utf-8")
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]
    _prepare(tmp_path, config)
    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)
    resume_engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [resume_engine]
    config.tuned = [resume_engine]
    _write_resume_manifest(tmp_path)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    original_materialize = runner_module.materialize_spsa_engine_configs

    def _materialize_then_mutate(**kwargs: object) -> None:
        original_materialize(**kwargs)
        engine_yaml.write_text(
            f"engine_path: {engine_binary}\noptions:\n  Threads: 2\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(runner_module, "materialize_spsa_engine_configs", _materialize_then_mutate)
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="changed during resume validation"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


def test_spsa_engine_materialization_archives_binary_and_path_resources(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    model_path = external_dir / "model.nnue"
    helper_path = external_dir / "engine-helper.dll"
    engine_binary.write_bytes(b"engine-v1")
    model_path.write_bytes(b"model-v1")
    helper_path.write_bytes(b"helper-v1")
    engine_yaml = external_dir / "engine.yaml"
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\nworking_directory: {external_dir}\noptions:\n  DNN_Model: {model_path}\n",
        encoding="utf-8",
    )
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]

    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)

    assert engine.engine_path is not None
    assert engine.engine_path.is_relative_to(tmp_path / "inputs" / "engine_configs")
    archived = UsiEngineConfig.from_file(engine.engine_path)
    assert archived.engine_path is not None
    assert Path(archived.engine_path).read_bytes() == b"engine-v1"
    assert Path(str(archived.options["DNN_Model"])).read_bytes() == b"model-v1"
    assert archived.working_directory is not None
    archived_helper = Path(archived.working_directory) / helper_path.name
    assert archived_helper.read_bytes() == b"helper-v1"
    engine_binary.write_bytes(b"engine-v2")
    model_path.write_bytes(b"model-v2")
    helper_path.write_bytes(b"helper-v2")
    assert Path(archived.engine_path).read_bytes() == b"engine-v1"
    assert Path(str(archived.options["DNN_Model"])).read_bytes() == b"model-v1"
    assert archived_helper.read_bytes() == b"helper-v1"


def test_spsa_engine_materialization_seals_declared_overlay_options(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    model_path = external_dir / "model.nnue"
    overlay_path = external_dir / "options.yaml"
    engine_binary.write_bytes(b"engine")
    model_path.write_bytes(b"model-v1")
    overlay_path.write_text(
        f"options:\n  Threads: 1\n  DNN_Model: {model_path}\n",
        encoding="utf-8",
    )
    engine_yaml = external_dir / "engine.yaml"
    engine_yaml.write_text(f"engine_path: {engine_binary}\n", encoding="utf-8")
    engine = EngineConfig(
        name="engine",
        engine_path=engine_yaml,
        options={"Threads": 1, "DNN_Model": str(model_path)},
        options_overlays=[overlay_path],
    )
    config.baseline = [engine]
    config.tuned = [engine]

    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)

    assert engine.options_overlays == []
    assert engine.options["Threads"] == 1
    archived_model = Path(str(engine.options["DNN_Model"]))
    assert archived_model.read_bytes() == b"model-v1"
    overlay_path.write_text(
        f"options:\n  Threads: 2\n  DNN_Model: {model_path}\n  NewOption: 7\n",
        encoding="utf-8",
    )
    model_path.write_bytes(b"model-v2")
    assert engine.options["Threads"] == 1
    assert "NewOption" not in engine.options
    assert archived_model.read_bytes() == b"model-v1"


def test_spsa_engine_materialization_rejects_working_directory_containing_run(
    tmp_path: Path,
) -> None:
    config = _write_inputs(tmp_path)
    engine_binary = tmp_path.parent / f"{tmp_path.name}-engine.exe"
    engine_binary.write_bytes(b"engine")
    engine_yaml = tmp_path.parent / f"{tmp_path.name}-engine.yaml"
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\nworking_directory: {tmp_path.parent}\n",
        encoding="utf-8",
    )
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="working_directory must not contain the run archive"):
        materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_production_resume_rejects_working_directory_containing_run_before_writes(
    tmp_path: Path,
) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    engine_binary.write_bytes(b"engine")
    engine_yaml = external_dir / "engine.yaml"
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\nworking_directory: {external_dir}\n",
        encoding="utf-8",
    )
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]
    _prepare(tmp_path, config)
    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\nworking_directory: {tmp_path.parent}\n",
        encoding="utf-8",
    )
    resume_engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [resume_engine]
    config.tuned = [resume_engine]
    _write_resume_manifest(tmp_path)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="working_directory must not contain the run archive"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_new_artifact_engine_overlap_rejection_keeps_run_tree_bytes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    engine_binary = tmp_path.parent / f"{tmp_path.name}-artifact-engine.exe"
    engine_binary.write_bytes(b"engine")
    engine = EngineConfig(name="engine", artifact="repo/abcdef", build_options={})
    config.baseline = [engine]
    config.tuned = [engine]
    runner = _runner(tmp_path, config)
    runner._engine_factory_service = SimpleNamespace(
        artifact_resolver=lambda artifact, build_options: engine_binary,
    )
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="working_directory must not contain the run archive"):
        await runner.prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
async def test_new_artifact_engine_rejects_mismatched_unpublished_archive_without_writes(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-artifact-runtime"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    engine_binary.write_bytes(b"engine")
    engine = EngineConfig(name="engine", artifact="repo/abcdef", build_options={})
    config.baseline = [engine]
    config.tuned = [engine]
    published_root = tmp_path / "inputs" / "engine_configs"
    published_root.mkdir(parents=True)
    (published_root / "tampered.yaml").write_bytes(b"tampered")
    runner = _runner(tmp_path, config)
    runner._engine_factory_service = SimpleNamespace(
        artifact_resolver=lambda artifact, build_options: engine_binary,
    )
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="unpublished engine archive does not match"):
        await runner.prepare_domain()

    assert _tree_bytes(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ["binary", "model"])
async def test_production_resume_rejects_external_runtime_resource_mutation_before_writes(
    tmp_path: Path,
    resource_kind: str,
) -> None:
    config = _write_inputs(tmp_path)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir()
    engine_binary = external_dir / "engine.exe"
    model_path = external_dir / "model.nnue"
    engine_binary.write_bytes(b"engine-v1")
    model_path.write_bytes(b"model-v1")
    engine_yaml = external_dir / "engine.yaml"
    engine_yaml.write_text(
        f"engine_path: {engine_binary}\noptions:\n  DNN_Model: {model_path}\n",
        encoding="utf-8",
    )
    engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [engine]
    config.tuned = [engine]
    _prepare(tmp_path, config)
    materialize_spsa_engine_configs(config=config, run_dir=tmp_path, artifact_resolver=None)
    resume_engine = EngineConfig(name="engine", engine_path=engine_yaml)
    config.baseline = [resume_engine]
    config.tuned = [resume_engine]
    _write_resume_manifest(tmp_path)
    ledger, _runtime = _initialize_resume_ledger(tmp_path, config)
    ledger.close()
    storage = FilesystemRunStorage(tmp_path)
    database = storage.db_service()
    database.ensure_schema()
    database.close()
    mutation_target = engine_binary if resource_kind == "binary" else model_path
    mutation_target.write_bytes(f"{resource_kind}-v2".encode())
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="resolved engine provenance"):
        await _runner(tmp_path, config, storage=storage).prepare_domain()

    assert _tree_bytes(tmp_path) == before


def test_prepare_spsa_domain_inputs_rebuilds_inconsistent_current_from_ledger(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _create_ledger(tmp_path)
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["completed_updates"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    current_path = tmp_path / "spsa" / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "schema_version": "shogiarena.spsa.current.v1",
                "update_idx": 0,
                "pair_index_end": 0,
                "theta": {"tempo": 10.0},
            }
        ),
        encoding="utf-8",
    )
    _prepare(tmp_path, config, completed_updates=1, theta={"tempo": 12.0})

    rebuilt = json.loads(current_path.read_text(encoding="utf-8"))
    assert rebuilt["update_idx"] == 1
    assert rebuilt["theta"] == {"tempo": 12.0}


def test_prepare_spsa_domain_inputs_rebuilds_state_hash_from_sealed_ledger_contract(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    _create_ledger(tmp_path)
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["resume_hash"] = "different-run"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _prepare(tmp_path, config, completed_updates=0, theta={"tempo": 10.0})

    rebuilt = json.loads(state_path.read_text(encoding="utf-8"))
    assert rebuilt["resume_hash"] == "resume-hash"
    assert rebuilt["completed_updates"] == 0
