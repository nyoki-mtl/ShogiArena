from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.game_item_builders import (
    build_spsa_game_items,
    build_tournament_game_items,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def _engine_config(name: str, path: Path | None, *, time_control: TimeControlLimits | None = None) -> EngineConfig:
    return EngineConfig(name=name, engine_path=path, time_control=time_control)


def test_build_tournament_game_items_builds_specs_and_limits(tmp_path: Path) -> None:
    base_tc = TimeControlLimits(time_ms=60_000)
    black_cfg = _engine_config("black", tmp_path / "black.yaml")
    white_cfg = _engine_config("white", tmp_path / "white.yaml")

    prepared = build_tournament_game_items(
        game_id="g-001",
        black_engine_name="black",
        white_engine_name="white",
        engine_configs={"black": black_cfg, "white": white_cfg},
        extra_options={"Threads": "1"},
        base_time_control=base_tc,
        black_instance_override="ssh-a",
        white_instance_override="ssh-b",
        engine_game_spec_fn=lambda pool_key, config_path, extra_options, instance_override, role: {
            "pool_key": pool_key,
            "config_path": str(config_path),
            "extra_options": extra_options,
            "instance_override": instance_override,
            "role": role,
        },
    )

    assert prepared.black_item["pool_key"] == "black#black"
    assert prepared.white_item["pool_key"] == "white#white"
    assert prepared.black_item["instance_override"] == "ssh-a"
    assert prepared.white_item["instance_override"] == "ssh-b"
    assert prepared.black_item["extra_options"] == {"Threads": "1"}
    assert prepared.white_item["extra_options"] == {"Threads": "1"}
    assert prepared.black_limits.time_ms == 60_000
    assert prepared.white_limits.time_ms == 60_000


def test_build_tournament_game_items_rejects_missing_engine_path(tmp_path: Path) -> None:
    base_tc = TimeControlLimits(time_ms=60_000)
    black_cfg = _engine_config("black", None)
    white_cfg = _engine_config("white", tmp_path / "white.yaml")

    with pytest.raises(ValueError, match="Engine 'black' is missing a resolved engine_path"):
        build_tournament_game_items(
            game_id="g-002",
            black_engine_name="black",
            white_engine_name="white",
            engine_configs={"black": black_cfg, "white": white_cfg},
            extra_options=None,
            base_time_control=base_tc,
            black_instance_override=None,
            white_instance_override=None,
            engine_game_spec_fn=lambda pool_key, config_path, extra_options, instance_override, role: {
                "pool_key": pool_key,
                "config_path": str(config_path),
                "extra_options": extra_options,
                "instance_override": instance_override,
                "role": role,
            },
        )


def test_build_spsa_game_items_applies_tuned_black_and_time_control_override(tmp_path: Path) -> None:
    base_tc = TimeControlLimits(time_ms=30_000)
    override_tc = TimeControlLimits(time_ms=15_000, increment_ms=100)
    base_spec = _engine_config("base", tmp_path / "base.yaml")
    tuned_spec = _engine_config("tuned", tmp_path / "tuned.yaml")

    prepared = build_spsa_game_items(
        is_tuned_as_black=True,
        base_spec=base_spec,
        tuned_spec=tuned_spec,
        baseline_name="base",
        tuned_name="tuned",
        baseline_config_path=tmp_path / "base.yaml",
        tuned_config_path=tmp_path / "tuned.yaml",
        extra_options={"Ponder": "false"},
        base_time_control=base_tc,
        time_control_override=override_tc,
        engine_game_spec_fn=lambda pool_key, config_path, extra_options: {
            "pool_key": pool_key,
            "config_path": str(config_path),
            "extra_options": extra_options,
        },
    )

    assert prepared.black_item["pool_key"] == "tuned#tuned"
    assert prepared.white_item["pool_key"] == "base#baseline"
    assert prepared.black_item["extra_options"] == {"Ponder": "false"}
    assert prepared.white_item["extra_options"] == {"Ponder": "false"}
    assert prepared.black_limits.time_ms == 15_000
    assert prepared.white_limits.time_ms == 15_000
    assert prepared.black_limits.increment_ms == 100
    assert prepared.white_limits.increment_ms == 100


def test_build_spsa_game_items_requires_time_control_when_missing(tmp_path: Path) -> None:
    base_spec = _engine_config("base", tmp_path / "base.yaml", time_control=None)
    tuned_spec = _engine_config("tuned", tmp_path / "tuned.yaml", time_control=None)

    with pytest.raises(RuntimeError, match="Missing required time_control for SPSA engine 'base'"):
        build_spsa_game_items(
            is_tuned_as_black=False,
            base_spec=base_spec,
            tuned_spec=tuned_spec,
            baseline_name="base",
            tuned_name="tuned",
            baseline_config_path=tmp_path / "base.yaml",
            tuned_config_path=tmp_path / "tuned.yaml",
            extra_options=None,
            base_time_control=None,
            time_control_override=None,
            engine_game_spec_fn=lambda pool_key, config_path, extra_options: {
                "pool_key": pool_key,
                "config_path": str(config_path),
                "extra_options": extra_options,
            },
        )
