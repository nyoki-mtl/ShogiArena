from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import RunOptions
from shogiarena._core.contexts.spsa.adapters import runner as runner_module
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.orchestrator_update_mixin import (
    SpsaOrchestratorUpdateMixin,
)
from shogiarena._core.contexts.spsa.adapters.runner import (
    SpsaRunner,
    _resolve_spsa_run_seed,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.domain.versioned_rng import (
    SPSA_RNG_SCHEMA,
    HmacSha256Rng,
    SpsaRngDomain,
)

RUN_SEED = "00" * 32


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("spsa.flip", "8ef6b93a4f87deb3a14217d2046eeb25fe65b8ee023db1a352a39ebfbc46d85d"),
        ("spsa.opening", "3e62aaa4fe5840484f82f9c38fd738fa61f8f12410f8b7678d3e51ef0964efd0"),
        ("spsa.rounding", "1d546934d46eb8ade3dbe6f4fb43b9196721dbf1aa9170e24e24d944853e9fc1"),
        ("spsa.retry", "373719ef1ecdb15a4236b876fe591e69438a0d9b5861cf5d11ad699e60f551bc"),
        ("spsa.node_multiplier", "aed2f292d6a709f870accb833d4716d75dbf8bd78eba054d631e7f2e9717fb0a"),
    ],
)
def test_hmac_sha256_rng_golden_vectors(domain: SpsaRngDomain, expected: str) -> None:
    rng = HmacSha256Rng(
        seed_hex=RUN_SEED,
        domain=domain,
        run_id="run-1",
        update_idx=7,
        pair_idx=3,
        parameter_id="Threads",
    )

    assert rng.next_block().hex() == expected


def test_hmac_rng_assignments_are_order_and_restart_invariant() -> None:
    def assignment(order: list[int]) -> dict[int, int]:
        return {
            pair_idx: HmacSha256Rng(
                seed_hex=RUN_SEED,
                domain="spsa.opening",
                run_id="run-1",
                update_idx=4,
                pair_idx=pair_idx,
            ).randrange(97)
            for pair_idx in order
        }

    expected = assignment([0, 1, 2, 3])
    assert assignment([3, 1, 0, 2]) == expected
    assert assignment([0, 1, 2, 3]) == expected


def test_hmac_rng_identity_fields_are_domain_separated() -> None:
    base = HmacSha256Rng(RUN_SEED, "spsa.flip", "run-1", 1, 0, "Threads").next_block()
    variants = {
        HmacSha256Rng(RUN_SEED, "spsa.retry", "run-1", 1, 0, "Threads").next_block(),
        HmacSha256Rng(RUN_SEED, "spsa.flip", "run-2", 1, 0, "Threads").next_block(),
        HmacSha256Rng(RUN_SEED, "spsa.flip", "run-1", 2, 0, "Threads").next_block(),
        HmacSha256Rng(RUN_SEED, "spsa.flip", "run-1", 1, 1, "Threads").next_block(),
        HmacSha256Rng(RUN_SEED, "spsa.flip", "run-1", 1, 0, "Hash").next_block(),
    }

    assert len(variants) == 5
    assert base not in variants


def test_orchestrator_rng_does_not_depend_on_space_or_run_path(tmp_path: Path) -> None:
    def helper(path_name: str, space_path: str) -> SpsaOrchestratorUpdateMixin:
        instance = object.__new__(SpsaOrchestratorUpdateMixin)
        instance.config = SimpleNamespace(
            run_seed=RUN_SEED,
            experiment_name="stable-run-id",
            space_path=space_path,
        )
        instance.run_dir = tmp_path / path_name
        return instance

    original = helper("original", "C:/first/space.yaml")
    relocated = helper("renamed", "D:/relocated/space.yaml")

    assert (
        original._make_rng(
            domain="spsa.flip",
            update_idx=9,
            pair_idx=2,
            parameter_id="Threads",
        ).next_block()
        == relocated._make_rng(
            domain="spsa.flip",
            update_idx=9,
            pair_idx=2,
            parameter_id="Threads",
        ).next_block()
    )


@pytest.mark.asyncio
async def test_prepare_run_dir_generates_and_persists_256_bit_seed(tmp_path: Path) -> None:
    class _Config:
        run_seed: str | None = None
        experiment_name = "run-1"

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "experiment_name": self.experiment_name,
                "run_seed": self.run_seed,
                "baseline": [],
                "tuned": [],
                "num_updates": 1,
            }

    runner = object.__new__(SpsaRunner)
    runner.config = _Config()
    runner.run_dir = tmp_path
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = RunMetadataPersistenceService()

    await runner.prepare_run_dir()

    assert runner.config.run_seed is not None
    assert len(bytes.fromhex(runner.config.run_seed)) == 32
    resolved = yaml.safe_load((tmp_path / "inputs" / "config_resolved.yaml").read_text(encoding="utf-8"))
    assert resolved["run_seed"] == runner.config.run_seed
    manifest = yaml.safe_load((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spsa"]["run_seed"] == runner.config.run_seed


@pytest.mark.asyncio
async def test_prepare_run_dir_recovers_current_ledger_before_seed_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Config:
        run_seed: str | None = None
        experiment_name = "run-1"

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {}

    (tmp_path / "state.json").write_text("{}\n", encoding="utf-8")
    ledger_path = tmp_path / "spsa" / "ledger.sqlite3"
    ledger_path.parent.mkdir()
    ledger_path.touch()
    calls: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "recover_spsa_ledger",
        lambda _run_dir: calls.append("recover"),
    )

    def _resolve(**_kwargs: object) -> str:
        calls.append("read-seed")
        return RUN_SEED

    monkeypatch.setattr(runner_module, "_resolve_spsa_run_seed", _resolve)
    monkeypatch.setattr(
        runner_module,
        "prepare_spsa_run_directory",
        lambda **_kwargs: (tmp_path, {}),
    )
    runner = object.__new__(SpsaRunner)
    runner.config = _Config()
    runner.run_dir = tmp_path
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = RunMetadataPersistenceService()

    await runner.prepare_run_dir()

    assert calls == ["recover", "read-seed"]


def test_resume_seed_is_loaded_read_only_and_mismatch_fails(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{}\n", encoding="utf-8")
    ledger = open_spsa_ledger(tmp_path)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="run-1")
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed=RUN_SEED,
        contract={"run_seed": RUN_SEED},
        params=[
            ParamEntry(
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
        ],
    )
    assert ledger.connection.execute(
        "SELECT rng_schema, sealed_run_seed FROM run_contract WHERE run_id = 'run-1'"
    ).fetchone() == (SPSA_RNG_SCHEMA, RUN_SEED)
    ledger.close()
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert (
        _resolve_spsa_run_seed(
            configured_seed=None,
            run_dir=tmp_path,
            should_skip_resume=False,
            experiment_name="run-1",
        )
        == RUN_SEED
    )
    with pytest.raises(ValueError, match="does not match"):
        _resolve_spsa_run_seed(
            configured_seed="ff" * 32,
            run_dir=tmp_path,
            should_skip_resume=False,
            experiment_name="run-1",
        )

    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_resume_rejects_pre_versioned_rng_contract(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{}\n", encoding="utf-8")
    ledger = open_spsa_ledger(tmp_path)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="run-1")
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed=RUN_SEED,
        contract={"run_seed": RUN_SEED},
        params=[
            ParamEntry("Threads", "int", 1.0, 1.0, 8.0, 1.0, 1.0, "", False),
        ],
    )
    ledger.connection.execute(
        "UPDATE run_contract SET rng_schema = ? WHERE run_id = 'run-1'",
        ("shogiarena.spsa.rng.resume-hash.v1",),
    )
    ledger.connection.commit()
    ledger.close()

    with pytest.raises(ValueError, match="unsupported RNG schema"):
        _resolve_spsa_run_seed(
            configured_seed=None,
            run_dir=tmp_path,
            should_skip_resume=False,
            experiment_name="run-1",
        )


def test_rng_schema_identifier_is_versioned() -> None:
    assert SPSA_RNG_SCHEMA == "shogiarena.spsa.hmac-sha256.v1"
