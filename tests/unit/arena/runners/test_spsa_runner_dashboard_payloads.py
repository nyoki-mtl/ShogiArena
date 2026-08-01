from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from shogiarena._core.contexts.dashboard.adapters.spsa.params_service import SpsaParamsService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import (
    compute_engine_time_control_specs,
    engine_instance_defaults,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_parser import parse_spsa_config_mapping
from shogiarena._core.contexts.spsa.adapters.runner_dashboard_payloads import (
    seed_spsa_initial_summary,
    spsa_engine_configs,
)
from shogiarena._core.contexts.spsa.application.space_spec import (
    load_spsa_space_spec,
    persist_normalized_space,
)
from shogiarena._core.interfaces.cli.config_file_loaders import parse_spsa_config_file


class _ApiServerStub:
    def __init__(self) -> None:
        self.summary_updates: list[tuple[dict[str, object], str]] = []

    def broadcast_summary_update(self, payload: dict[str, object], *, source: str) -> None:
        self.summary_updates.append((payload, source))


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def _load_spsa_run_config(config_path: Path):
    payload = parse_spsa_config_file(config_path)
    return parse_spsa_config_mapping(payload, source_path=config_path)


def _write_space(tmp_path: Path) -> Path:
    return _write(
        tmp_path,
        "cfg/space.yaml",
        """
        schema_version: shogiarena.spsa.space.v1
        target:
          engine_family: test
          protocol: usi_options
          required_options_policy: strict
          tunable_manifest:
            required: false
            command: usi_tunables
        parameters:
          - id: p1
            target:
              option: p1
              value_encoding: decimal
            value_type: float
            initial: 1.0
            bounds:
              min: 0.0
              max: 10.0
            schedule:
              c_end: 1.0
              r_end: 0.1
        """,
    )


def test_seed_spsa_initial_summary_uses_canonical_engine_catalog_helpers(tmp_path: Path) -> None:
    engine_yaml = _write(
        tmp_path,
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    space_yaml = _write_space(tmp_path)
    config_yaml = _write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_yaml))}
            name: seed
        rules:
          initial_positions:
            type: file
            source: {tmp_path}/sfens.txt
          time_control:
            node_limit: 100
        spsa:
          space: {space_yaml}
          num_updates: 1
          num_parallel: 1
        """,
    )
    _write(tmp_path, "sfens.txt", "startpos\n")

    config = _load_spsa_run_config(config_yaml)
    baseline = config.baseline[0].model_copy(deep=True, update={"name": "baseline", "instance_id": "local"})
    tuned = config.tuned[0].model_copy(deep=True, update={"name": "tuned", "instance_id": "remote-1"})
    config = config.model_copy(update={"baseline": [baseline], "tuned": [tuned]})
    api_server = _ApiServerStub()
    preflight_dir = tmp_path / "spsa"
    preflight_dir.mkdir()
    (preflight_dir / "fixed_option_preflight.json").write_text(
        json.dumps({"status": "passed"}),
        encoding="utf-8",
    )

    seed_spsa_initial_summary(
        run_dir=tmp_path,
        config=config,
        num_workers=2,
        params=None,
        experiment_initial_params=None,
        session_uuid="session-001",
        session_started_at_iso="2026-01-01T00:00:00",
        api_server=api_server,
        engine_metadata=[{"name": "baseline"}, {"name": "tuned"}],
        rules_payload={},
        spsa_algorithm_config={},
    )

    engines = spsa_engine_configs(config)
    expected_time_controls, expected_default_time_control = compute_engine_time_control_specs(config.rules, engines)
    expected_instances = engine_instance_defaults(engines)
    summary_payload, source = api_server.summary_updates[0]
    meta_payload = json.loads((tmp_path / "spsa" / "meta.json").read_text(encoding="utf-8"))

    assert source == "spsa"
    assert summary_payload["engine_time_controls"] == expected_time_controls
    assert summary_payload["default_time_control"] == expected_default_time_control
    assert summary_payload["engine_instances"] == expected_instances
    assert summary_payload["preflight_status"] == {"fixed_options": "passed"}
    assert meta_payload["engine_time_controls"] == expected_time_controls
    assert meta_payload["default_time_control"] == expected_default_time_control
    assert meta_payload["engine_instances"] == expected_instances
    assert meta_payload["preflight_status"] == {"fixed_options": "passed"}


def test_seed_spsa_initial_summary_writes_meta_without_api_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_yaml = _write(tmp_path, "cfg/engine.yaml", 'engine_path: "/bin/echo"\n')
    space_yaml = _write_space(tmp_path)
    config_yaml = _write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: archive-only
        engines:
          - engine_path: {json.dumps(str(engine_yaml))}
            name: tuned
        rules:
          initial_positions:
            type: file
            source: {tmp_path}/sfens.txt
          time_control:
            node_limit: 1
        spsa:
          space: {space_yaml}
          num_updates: 1
          num_parallel: 1
        """,
    )
    _write(tmp_path, "sfens.txt", "startpos\n")
    config = _load_spsa_run_config(config_yaml)
    space = load_spsa_space_spec(space_yaml)
    persist_normalized_space(tmp_path, space)

    seed_spsa_initial_summary(
        run_dir=tmp_path,
        config=config,
        num_workers=1,
        params=space.to_param_entries(),
        experiment_initial_params={"p1": 1.0},
        session_uuid="session-disabled",
        session_started_at_iso="2026-01-01T00:00:00",
        api_server=None,
        engine_metadata=[],
        rules_payload={},
        spsa_algorithm_config={},
    )

    meta_payload = json.loads((tmp_path / "spsa" / "meta.json").read_text(encoding="utf-8"))
    assert meta_payload["experiment_name"] == "archive-only"
    assert meta_payload["session_uuid"] == "session-disabled"
    store = SpsaStore(run_dir=tmp_path)
    params_payload = SpsaParamsService(store=store, run_dir=tmp_path).build_params_payload()
    assert params_payload["initial_params"] == {"p1": 1.0}

    resumed_params = space.to_param_entries()
    resumed_params[0].value = 2.0
    meta_path = tmp_path / "spsa" / "meta.json"
    prior_bytes = meta_path.read_bytes()
    real_replace = Path.replace

    def _fail_meta_replace(path: Path, target: Path) -> Path:
        if path.name == ".meta.json.tmp":
            raise OSError("injected replace failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", _fail_meta_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        seed_spsa_initial_summary(
            run_dir=tmp_path,
            config=config,
            num_workers=1,
            params=resumed_params,
            experiment_initial_params={"p1": 1.0},
            session_uuid="session-resumed",
            session_started_at_iso="2026-01-02T00:00:00",
            api_server=None,
            engine_metadata=[],
            rules_payload={},
            spsa_algorithm_config={},
        )
    assert meta_path.read_bytes() == prior_bytes

    monkeypatch.setattr(Path, "replace", real_replace)
    seed_spsa_initial_summary(
        run_dir=tmp_path,
        config=config,
        num_workers=1,
        params=resumed_params,
        experiment_initial_params={"p1": 1.0},
        session_uuid="session-resumed",
        session_started_at_iso="2026-01-02T00:00:00",
        api_server=None,
        engine_metadata=[],
        rules_payload={},
        spsa_algorithm_config={},
    )

    resumed_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert resumed_meta["experiment_initial_params"] == {"p1": 1.0}
    assert resumed_meta["session_start_params"] == {"p1": 2.0}
    assert [session["session_uuid"] for session in resumed_meta["sessions"]] == [
        "session-disabled",
        "session-resumed",
    ]
