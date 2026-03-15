from __future__ import annotations

import json
import textwrap
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import (
    compute_engine_time_control_specs,
    engine_instance_defaults,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_parser import parse_spsa_config_mapping
from shogiarena._core.contexts.spsa.adapters.runner_dashboard_payloads import (
    seed_spsa_initial_summary,
    spsa_engine_configs,
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
    config_yaml = _write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: "{engine_yaml}"
            name: seed
        rules:
          initial_positions:
            type: file
            source: {tmp_path}/sfens.txt
          time_control:
            node_limit: 100
        spsa:
          parameters_path: {tmp_path}/params.txt
          num_updates: 1
          num_parallel: 1
        """,
    )
    _write(tmp_path, "sfens.txt", "startpos\n")
    _write(tmp_path, "params.txt", "# empty\n")

    config = _load_spsa_run_config(config_yaml)
    baseline = config.baseline[0].model_copy(deep=True, update={"name": "baseline", "instance_id": "local"})
    tuned = config.tuned[0].model_copy(deep=True, update={"name": "tuned", "instance_id": "remote-1"})
    config = config.model_copy(update={"baseline": [baseline], "tuned": [tuned]})
    api_server = _ApiServerStub()

    seed_spsa_initial_summary(
        run_dir=tmp_path,
        config=config,
        num_workers=2,
        params=None,
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
    assert summary_payload["engineTimeControls"] == expected_time_controls
    assert summary_payload["defaultTimeControl"] == expected_default_time_control
    assert summary_payload["engineInstances"] == expected_instances
    assert meta_payload["engine_time_controls"] == expected_time_controls
    assert meta_payload["default_time_control"] == expected_default_time_control
    assert meta_payload["engine_instances"] == expected_instances
