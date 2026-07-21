from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import RunOptions
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import prepare_spsa_domain_inputs
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState


class _Config(SimpleNamespace):
    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"experiment_name": "resume-test"}


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
        update_mode="classic",
        is_snap_float_to_step=False,
        early_stop=None,
        inflight_factor=1,
    )


def _prepare(tmp_path: Path, config: _Config):
    return prepare_spsa_domain_inputs(
        config=config,
        run_dir=tmp_path,
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
    )


def test_prepare_spsa_domain_inputs_restores_theta_and_skips_completed_updates(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    initial_params, _sfens, initial_items = _prepare(tmp_path, config)
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
    current_before = current_path.read_bytes()

    restored_params, sfens, remaining_items = _prepare(tmp_path, config)

    assert restored_params[0].value == 17.5
    assert sfens == ["startpos"]
    assert remaining_items == [3]
    assert current_path.read_bytes() == current_before
    assert json.loads(state_path.read_text(encoding="utf-8"))["completed_updates"] == 2


@pytest.mark.asyncio
async def test_spsa_runner_prepare_domain_consumes_restored_state(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
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

    class _Metadata:
        def seal_provenance_manifest(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(hashes=SimpleNamespace(schedule_hash="schedule-hash", resume_hash="resume-hash"))

    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner.config = config
    runner._state = SpsaRunnerState()
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = _Metadata()
    runner._frozen_run_config_payload = {"experiment_name": "resume-test"}
    runner._engine_factory_service = SimpleNamespace(artifact_resolver=None)

    await runner.prepare_domain()

    assert runner._state.params is not None
    assert runner._state.params[0].value == 17.5
    assert runner._state.update_items == [3]


def test_prepare_spsa_domain_inputs_rejects_inconsistent_current_without_overwriting_it(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
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
    current_before = current_path.read_bytes()

    with pytest.raises(ValueError, match="current.json disagrees.*no-resume"):
        _prepare(tmp_path, config)

    assert current_path.read_bytes() == current_before


def test_prepare_spsa_domain_inputs_rejects_resume_hash_mismatch_without_rewriting_state(tmp_path: Path) -> None:
    config = _write_inputs(tmp_path)
    _prepare(tmp_path, config)
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["resume_hash"] = "different-run"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_before = state_path.read_bytes()

    with pytest.raises(ValueError, match="resume_hash.*sealed run manifest"):
        _prepare(tmp_path, config)

    assert state_path.read_bytes() == state_before
