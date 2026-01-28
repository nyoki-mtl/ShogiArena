import textwrap
from pathlib import Path

import pytest

from shogiarena.arena.configs.spsa import load_config_yaml
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.orchestrators.spsa_orchestrator import SpsaOrchestrator
from shogiarena.arena.session import LifecycleHooksBase, SessionContext


def write(tmp: Path, rel: str, content: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_spsa_engine_initializes_with_global_time_control(tmp_path: Path) -> None:
    """Engine YAML lacks per-engine time_control but global rules.time_control exists."""
    eng_yaml = write(
        tmp_path,
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    cfg_yaml = write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_config: "{eng_yaml}"
            name: test
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
    write(tmp_path, "sfens.txt", "startpos\n")
    write(tmp_path, "params.txt", "# empty\n")

    cfg = load_config_yaml(str(cfg_yaml))
    # Ensure orchestrator initializes with global time_control only
    session = SessionContext.build(run_dir=tmp_path, num_workers=1, run_id="test")
    orch = SpsaOrchestrator(cfg, session=session, hooks=LifecycleHooksBase())
    assert orch is not None


def test_spsa_engine_with_time_control_initializes(tmp_path: Path) -> None:
    # Engine YAML with time_control should allow orchestrator initialization
    eng_yaml = write(
        tmp_path,
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        time_control:
          node_limit: 100
        """,
    )
    cfg_yaml = write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_config: "{eng_yaml}"
            name: test
        rules:
          initial_positions:
            type: file
            source: {tmp_path}/sfens.txt
        spsa:
          parameters_path: {tmp_path}/params.txt
          num_updates: 1
          num_parallel: 1
        """,
    )
    write(tmp_path, "sfens.txt", "startpos\n")
    write(tmp_path, "params.txt", "# empty\n")

    cfg = load_config_yaml(str(cfg_yaml))
    session = SessionContext.build(run_dir=tmp_path, num_workers=1, run_id="test")
    orch = SpsaOrchestrator(cfg, session=session, hooks=LifecycleHooksBase())
    assert orch is not None


def test_spsa_ltc_regression_config(tmp_path: Path) -> None:
    eng_yaml = write(
        tmp_path,
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    cfg_yaml = write(
        tmp_path,
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_config: "{eng_yaml}"
            name: test
        rules:
          initial_positions:
            type: file
            source: {tmp_path}/sfens.txt
        spsa:
          parameters_path: {tmp_path}/params.txt
          num_updates: 100
          num_parallel: 2
          ltc_regression:
            enabled: true
            every_n_updates: 5
            total_pairs: 12
            time_control:
              time_ms: 300000
              byoyomi_ms: 20000
            pass_criteria:
              min_winrate: 0.55
              max_elo_drop: -15
        """,
    )
    write(tmp_path, "sfens.txt", "startpos\n")
    write(tmp_path, "params.txt", "# empty\n")

    cfg = load_config_yaml(str(cfg_yaml))
    assert cfg.ltc_regression is not None
    ltc = cfg.ltc_regression
    assert ltc.enabled is True
    assert ltc.every_n_updates == 5
    assert ltc.total_pairs == 12
    assert isinstance(ltc.time_control, TimeControlLimits)
    assert ltc.time_control.time_ms == 300000
    assert ltc.time_control.byoyomi_ms == 20000
    assert ltc.pass_criteria is not None
    assert ltc.pass_criteria.min_winrate == 0.55
    assert ltc.pass_criteria.max_elo_drop == -15


def test_ltc_regression_rejects_removed_fail_action(tmp_path):
    sfens = tmp_path / "sfens.txt"
    sfens.write_text("startpos\n", encoding="utf-8")
    params = tmp_path / "params.txt"
    params.write_text("# empty\n", encoding="utf-8")
    engine_yaml = write(
        tmp_path,
        "engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )

    cfg_yaml = write(
        tmp_path,
        "cfg/spsa-invalid.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_config: "{engine_yaml}"
            name: test
        rules:
          initial_positions:
            type: file
            source: {sfens}
        spsa:
          parameters_path: {params}
          num_updates: 10
          ltc_regression:
            enabled: true
            every_n_updates: 5
            total_pairs: 10
            fail_action: stop
        """,
    )

    with pytest.raises(ValueError, match="fail_action is no longer supported"):
        load_config_yaml(str(cfg_yaml))
