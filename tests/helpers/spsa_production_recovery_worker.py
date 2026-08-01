from __future__ import annotations

import argparse
import asyncio
import copy
import os
from pathlib import Path
from types import MethodType, SimpleNamespace

import yaml
from spsa_crash_worker import _record_authority_games

import shogiarena._core.contexts.spsa.adapters.runner as runner_module
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import RunOptions
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    FIXED_OPTION_PREFLIGHT_FILENAME,
    FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
)
from shogiarena._core.contexts.spsa.adapters.ledger_observations import SpsaObservationLedger
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import (
    materialize_spsa_engine_configs,
    prepare_spsa_domain_inputs,
)
from shogiarena._core.contexts.spsa.adapters.tunable_manifest_preflight import (
    TUNABLE_HANDSHAKE_FILENAME,
    TUNABLE_HANDSHAKE_SCHEMA,
)
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.application.space_spec import load_spsa_space_spec
from shogiarena._core.contexts.spsa.domain.ledger_models import SpsaUpdateState
from shogiarena._core.contexts.spsa.domain.versioned_rng import HmacSha256Rng
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.content_hashing import sha256_file
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256

_CRASH_EXIT = 87
_RUN_SEED = "02" * 32
_SCHEDULE_HASH = "a" * 64
_RESUME_HASH = "b" * 64


class _Config(SimpleNamespace):
    def model_copy(self, *, deep: bool) -> _Config:
        assert deep
        return _Config(**copy.deepcopy(vars(self)))

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"experiment_name": "run-1", "run_seed": _RUN_SEED}


class _Metadata:
    def build_sealed_hashes(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            config_fingerprint="inputs-hash",
            schedule_hash=_SCHEDULE_HASH,
            resume_hash=_RESUME_HASH,
        )

    def seal_provenance_manifest(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(hashes=SimpleNamespace(schedule_hash=_SCHEDULE_HASH, resume_hash=_RESUME_HASH))


class _CrashingMetadata(_Metadata):
    def seal_provenance_manifest(self, **_kwargs: object) -> SimpleNamespace:
        os._exit(_CRASH_EXIT)


class _ResultStore:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save_result(self, result: object) -> None:
        write_json_atomic(self._run_dir / "result.json", {"run_id": str(result.run_id)})


class _PreflightEngine:
    async def start(self) -> None:
        return

    def get_usi_options(self) -> dict[str, UsiOption]:
        return {
            "Threads": UsiOption(
                name="Threads",
                option_type="spin",
                default="1",
                minimum=1,
                maximum=8,
            ),
            "Clear Hash": UsiOption(name="Clear Hash", option_type="button"),
        }

    async def request_tunable_manifest(self, **_kwargs: object) -> None:
        return None

    async def close(self) -> None:
        return


class _PreflightFactory:
    async def create_engine(self, *_args: object, **_kwargs: object) -> _PreflightEngine:
        return _PreflightEngine()


def _config(run_dir: Path, *, with_artifact: bool = False) -> _Config:
    engine = EngineConfig(name="engine", artifact="repo/abcdef", build_options={})
    return _Config(
        experiment_name="run-1",
        run_seed=_RUN_SEED,
        space_path=run_dir / "space.yaml",
        start_sfens_path=run_dir / "sfens.txt",
        baseline=[engine] if with_artifact else [],
        tuned=[engine] if with_artifact else [],
        num_updates=1,
        pairs_per_update=1,
        scale=1.0,
        algorithm=SimpleNamespace(A=SimpleNamespace(mode="absolute", value=0.0)),
        algorithm_a=0.0,
        alpha=0.602,
        gamma=0.101,
        is_crn_enabled=True,
        int_rounding="none",
        int_ck_floor=0.0,
        is_snap_float_to_step=False,
        early_stop=None,
        inflight_factor=1,
        ltc_regression=None,
        system=SimpleNamespace(engine_handshake_timeout=2.0),
        variants=SimpleNamespace(apply=SimpleNamespace(is_clear_hash_enabled=True)),
    )


def _config_with_physical_engine(run_dir: Path) -> _Config:
    config = _config(run_dir)
    engine = EngineConfig(name="engine", engine_path=run_dir / "engine.yaml")
    config.baseline = [engine]
    config.tuned = [engine]
    return config


def _artifact_binary(run_dir: Path) -> Path:
    return run_dir.parent / f"{run_dir.name}-engine-runtime" / "engine.exe"


def _new_runner(run_dir: Path, *, metadata: _Metadata, with_artifact: bool) -> SpsaRunner:
    runner = object.__new__(SpsaRunner)
    runner.run_dir = run_dir
    runner.config = _config(run_dir, with_artifact=with_artifact)
    runner._storage = FilesystemRunStorage(run_dir)
    runner._state = SpsaRunnerState()
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = metadata
    runner._frozen_run_config_payload = {"experiment_name": "run-1"}
    runner.instance_pool = InstancePool()
    runner._engine_factory_service = EngineFactoryService(
        factory=_PreflightFactory(),  # type: ignore[arg-type]
        artifact_resolver=lambda artifact, build_options: _artifact_binary(run_dir),
    )
    return runner


async def crash_after_archive_publish(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    setup_inputs(run_dir)
    binary = _artifact_binary(run_dir)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"engine")
    runner = _new_runner(run_dir, metadata=_CrashingMetadata(), with_artifact=True)
    await runner.prepare_domain()
    raise AssertionError("archive publication crash point was not reached")


async def recover_archive_publish(run_dir: Path) -> None:
    runner = _new_runner(run_dir, metadata=_Metadata(), with_artifact=True)
    await runner.prepare_domain()
    write_json_atomic(
        run_dir / "archive-recovery-snapshot.json",
        {
            "archive": (run_dir / "inputs" / "engine_configs").is_dir(),
            "state": (run_dir / "state.json").is_file(),
            "ledger": (run_dir / "spsa" / "ledger.sqlite3").is_file(),
        },
    )


def setup_inputs(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "space.yaml").write_text(
        """
schema_version: shogiarena.spsa.space.v1
target:
  protocol: usi_options
parameters:
  - id: Threads
    target: {option: Threads, value_encoding: integer}
    value_type: int
    initial: 1
    bounds: {min: 1, max: 8}
    schedule: {c_end: 1, r_end: 1}
""".lstrip(),
        encoding="utf-8",
    )
    (run_dir / "sfens.txt").write_text("startpos\n", encoding="utf-8")
    binary = _artifact_binary(run_dir)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"engine")
    (run_dir / "engine.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "engine",
                "engine_path": str(binary),
                "working_directory": str(binary.parent),
            }
        ),
        encoding="utf-8",
    )


def setup(run_dir: Path, *, terminal: bool) -> None:
    setup_inputs(run_dir)
    config = _config_with_physical_engine(run_dir)
    materialize_spsa_engine_configs(
        config=config,  # type: ignore[arg-type]
        run_dir=run_dir,
        artifact_resolver=None,
    )
    params, _sfens, _updates = prepare_spsa_domain_inputs(
        config=config,
        run_dir=run_dir,
        schedule_hash=_SCHEDULE_HASH,
        resume_hash=_RESUME_HASH,
    )
    space = load_spsa_space_spec(config.space_path)
    handshake = {
        "schema_version": TUNABLE_HANDSHAKE_SCHEMA,
        "status": "passed",
        "response_status": "timeout_optional",
        "command": "usi_tunables",
        "required": False,
        "runtime_scope": "local_runtime",
        "instance_id": None,
        "engine_name": "engine",
        "manifest": None,
        "advertised_options": [
            {
                "name": "Threads",
                "type": "spin",
                "default": "1",
                "minimum": 1,
                "maximum": 8,
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
        ],
        "normalized_space": space.to_json(),
    }
    write_json_atomic(run_dir / "spsa" / TUNABLE_HANDSHAKE_FILENAME, handshake)
    fixed_option_evidence = {
        "schema_version": FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
        "status": "passed",
        "target_option_names": ["Threads"],
        "engines": [],
    }
    write_json_atomic(run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME, fixed_option_evidence)
    binary_digest = sha256_file(_artifact_binary(run_dir))
    engine_config_path = config.baseline[0].engine_path
    assert engine_config_path is not None
    engine_config_digest = sha256_file(engine_config_path)
    write_json_atomic(
        run_dir / "manifest.json",
        {
            "status": "provenance_sealed",
            "inputs_hash": "inputs-hash",
            "hashes": {"schedule_hash": _SCHEDULE_HASH, "resume_hash": _RESUME_HASH},
            "inputs": {
                "spsa_tunable_handshake": {
                    "path": "spsa/tunable_handshake.json",
                    "sha256": canonical_sha256(handshake),
                },
                "spsa_fixed_option_preflight": {
                    "path": "spsa/fixed_option_preflight.json",
                    "sha256": canonical_sha256(fixed_option_evidence),
                },
            },
            "engines": [
                {
                    "name": "baseline",
                    "artifact": "repo/abcdef",
                    "bytes_hash": {
                        "engine_binary_sha256": binary_digest,
                        "engine_config_sha256": engine_config_digest,
                        "path_options": {},
                    },
                },
                {
                    "name": "tuned",
                    "artifact": "repo/abcdef",
                    "bytes_hash": {
                        "engine_binary_sha256": binary_digest,
                        "engine_config_sha256": engine_config_digest,
                        "path_options": {},
                    },
                },
            ],
        },
    )
    storage = FilesystemRunStorage(run_dir)
    database = storage.db_service()
    database.ensure_schema()
    _record_authority_games(database, pair_id="spsa-u000001-p000000", kind="SPSA")
    ledger = open_spsa_ledger(run_dir)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="run-1")
    space_digest = canonical_sha256(space.to_json())
    runtime.initialize_run(
        resume_hash=_RESUME_HASH,
        space_digest=space_digest,
        sealed_run_seed=_RUN_SEED,
        contract={
            "resume_hash": _RESUME_HASH,
            "space_digest": space_digest,
            "config": config.model_dump(mode="json"),
        },
        params=params,
    )
    rng = HmacSha256Rng(
        seed_hex=_RUN_SEED,
        domain="spsa.flip",
        run_id="run-1",
        update_idx=1,
        parameter_id="Threads",
    )
    flip = 1 if rng.randint(0, 1) else -1
    plus_value = max(1, min(8, 1 + flip))
    minus_value = max(1, min(8, 1 - flip))
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule={
            "c": {"Threads": 1.0},
            "flips": {"Threads": flip},
            "expected_pair_ids": {
                "SPSA": ["spsa-u000001-p000000"],
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
        flips={"Threads": flip},
        rounding_samples={
            "plus_options": {"Threads": plus_value},
            "minus_options": {"Threads": minus_value},
        },
    )
    SpsaObservationLedger(ledger.connection).reconcile(run_id="run-1", source=database)
    database.close()
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_RUNNING)
    runtime.transition(update_idx=1, target=SpsaUpdateState.GAMES_COMPLETE)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={},
    )
    runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})
    if terminal:
        runtime.commit_terminal(status="clean", reason="completed", resumable=False)
    ledger.close()
    os._exit(_CRASH_EXIT)


async def _stop_runner(runner: SpsaRunner) -> None:
    await runner._stop_additional_services()


async def recover(run_dir: Path) -> None:
    config = _config_with_physical_engine(run_dir)
    runner = object.__new__(SpsaRunner)
    runner.run_dir = run_dir
    runner.config = config
    runner._storage = FilesystemRunStorage(run_dir)
    runner._state = SpsaRunnerState()
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = _Metadata()
    runner._frozen_run_config_payload = {"experiment_name": "run-1"}
    runner._engine_factory_service = SimpleNamespace(artifact_resolver=None)
    runner._result_store = _ResultStore(run_dir)
    runner._progress = SimpleNamespace(finalize=lambda _payload: None)
    runner.stop_services = MethodType(_stop_runner, runner)
    runner._init_dashboard_services = lambda: None

    class _NoDispatch:
        async def run_orchestrator(self, _orchestrator: object, _run_coro: object) -> None:
            raise AssertionError("finalization replay must not dispatch games")

    runner._run_controller = _NoDispatch()
    await runner.prepare_domain()
    await runner.init_services()

    async def _noop() -> None:
        return None

    await runner.run_orchestrator(SimpleNamespace(), _noop())
    original_builder = runner_module.build_spsa_final_result
    runner_module.build_spsa_final_result = lambda **_kwargs: SimpleNamespace(run_id="run-1")
    try:
        await runner.finalize_and_persist(None)
    finally:
        runner_module.build_spsa_final_result = original_builder
    write_json_atomic(
        run_dir / "recovery-snapshot.json",
        {
            "result": (run_dir / "result.json").exists(),
            "terminal": (run_dir / "spsa" / "terminal.json").exists(),
            "completion": (run_dir / "completion_status.json").exists(),
            "completed_flag": (run_dir / "completed.flag").exists(),
            "accepted_best": (run_dir / "spsa" / "accepted-best.json").exists(),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "action",
        choices=("setup", "recover", "archive-publish-crash", "archive-publish-recover"),
    )
    parser.add_argument("--terminal", action="store_true")
    args = parser.parse_args()
    if args.action == "setup":
        setup(args.run_dir, terminal=args.terminal)
    elif args.action == "archive-publish-crash":
        asyncio.run(crash_after_archive_publish(args.run_dir))
    elif args.action == "archive-publish-recover":
        asyncio.run(recover_archive_publish(args.run_dir))
    else:
        asyncio.run(recover(args.run_dir))


if __name__ == "__main__":
    main()
