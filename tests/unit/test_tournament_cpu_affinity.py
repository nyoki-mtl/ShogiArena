from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig


def _dummy_engine_path(tmp_path) -> str:
    cfg = tmp_path / "engine.yaml"
    cfg.write_text("name: dummy\n", encoding="utf-8")
    return str(cfg)


def test_engine_config_cpu_affinity_parses_string_ranges(tmp_path) -> None:
    spec = EngineConfig(
        engine_path=_dummy_engine_path(tmp_path),
        cpu_affinity="0,2-3,5",
    )
    assert spec.cpu_affinity == (0, 2, 3, 5)


def test_engine_config_cpu_affinity_parses_iterable(tmp_path) -> None:
    spec = EngineConfig(
        engine_path=_dummy_engine_path(tmp_path),
        cpu_affinity=[0, "1-2", 2, 4],
    )
    assert spec.cpu_affinity == (0, 1, 2, 4)


@pytest.mark.parametrize(
    "value",
    ["-1", "2--3", ["a"], "3-1"],
)
def test_engine_config_cpu_affinity_rejects_invalid_values(tmp_path, value) -> None:
    with pytest.raises((TypeError, ValueError)):
        EngineConfig(engine_path=_dummy_engine_path(tmp_path), cpu_affinity=value)
