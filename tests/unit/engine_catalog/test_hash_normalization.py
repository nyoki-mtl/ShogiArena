from __future__ import annotations

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    _build_artifact_config_filename,
)
from shogiarena._core.contexts.game_session.application.engine.config_hashing import hash_engine_config


def test_hash_normalization_keeps_engine_hash_and_artifact_filename_stable() -> None:
    config_a = EngineConfig.model_validate(
        {
            "artifact": "repo/abcdef12",
            "build_options": {"target": "avx2", "profile": "fast"},
            "options": {"Threads": 1, "Hash": 16},
        }
    )
    config_b = EngineConfig.model_validate(
        {
            "artifact": "repo/abcdef12",
            "build_options": {"profile": "fast", "target": "avx2"},
            "options": {"Hash": 16, "Threads": 1},
        }
    )

    assert hash_engine_config(config_a) == hash_engine_config(config_b)
    assert _build_artifact_config_filename(
        "repo/abcdef12",
        {"target": "avx2", "profile": "fast"},
    ) == _build_artifact_config_filename(
        "repo/abcdef12",
        {"profile": "fast", "target": "avx2"},
    )
