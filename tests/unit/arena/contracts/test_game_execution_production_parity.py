"""Local/Remote production spec builder parity tests (task 0054 Phase 2)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena import __version__
from shogiarena._core.contexts.game_session.adapters.orchestration import remote_control
from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import UsiOptionLayers
from shogiarena._core.contexts.game_session.adapters.orchestration.local_game_execution_contract import (
    resolve_local_game_execution,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import prepare_remote_game_spec
from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionSpec, TargetPlatform
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def _engine_config(tmp_path: Path, name: str) -> tuple[Path, Path]:
    binary = tmp_path / name
    binary.write_bytes(name.encode())
    config = tmp_path / f"{name}.yaml"
    config.write_text(
        "\n".join(
            [
                f"name: {name}",
                f'engine_path: "{binary.as_posix()}"',
                "engine_args: [--usi]",
                "environment:",
                "  PUBLIC_VALUE: visible",
                "options:",
                "  Hash: 64",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config, binary


@pytest.mark.asyncio
async def test_local_and_remote_production_builders_seal_identical_linux_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    black_config, black_binary = _engine_config(tmp_path, "black")
    white_config, white_binary = _engine_config(tmp_path, "white")
    layers = UsiOptionLayers(
        artifact_overlay={"Hash": 96},
        arena={"Hash": 128},
        declared_overlays={"Hash": 160},
        inline={"Hash": 192},
    )
    rules = SimpleNamespace(
        adjudication=SimpleNamespace(
            resign_threshold_cp=900,
            resign_move_count=5,
            is_resign_two_sided=True,
            is_max_plies_enabled=True,
            max_plies=240,
            should_sync_max_plies_with_engine=True,
        ),
        repetition_occurrences_to_draw=4,
    )
    limits = TimeControlLimits(fixed_time_ms=100)

    class _Executor:
        worker_bundle = SimpleNamespace(manifest=SimpleNamespace(package_version=__version__))

        async def provision_game_engine_artifacts(self, **_kwargs: object) -> None:
            return None

    executor = _Executor()

    monkeypatch.setattr(remote_control, "get_remote_executor", lambda *_args, **_kwargs: executor)

    async def ensure_repo(*_args: object, **_kwargs: object) -> str:
        return "~/arena"

    async def resolve_binaries(*_args: object) -> list[Path]:
        return [black_binary, white_binary]

    monkeypatch.setattr(remote_control, "ensure_remote_deployment", ensure_repo)
    monkeypatch.setattr(remote_control, "_resolve_remote_binaries", resolve_binaries)

    common = {
        "run_id": "run",
        "game_id": "game",
        "black_name": "black",
        "white_name": "white",
        "black_config_path": black_config,
        "white_config_path": white_config,
        "black_variant_options": {"ParamA": 10},
        "white_variant_options": {"ParamA": -10},
        "black_variant_id": "plus",
        "white_variant_id": "minus",
        "black_path_option_names": (),
        "white_path_option_names": (),
        "black_go_options": {"nodes": 123},
        "white_go_options": {"depth": 7},
        "black_handshake_timeout_s": 12.0,
        "white_handshake_timeout_s": 13.0,
        "black_limits": limits,
        "white_limits": limits,
        "rules": rules,
        "timeout_reclassification_enabled": True,
    }
    local = resolve_local_game_execution(
        initial_sfen="startpos",
        black_artifact_overlay_options=layers.artifact_overlay,
        white_artifact_overlay_options=layers.artifact_overlay,
        black_arena_options=layers.arena,
        white_arena_options=layers.arena,
        black_overlay_options=layers.declared_overlays,
        white_overlay_options=layers.declared_overlays,
        black_inline_options=layers.inline,
        white_inline_options=layers.inline,
        clear_hash_before_game=False,
        after_variant_setoption="none",
        engine_lifecycle="per_game",
        target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
        **common,
    )
    _executor, _root, remote_payload = await prepare_remote_game_spec(
        SimpleNamespace(run_dir=tmp_path / "run"),
        remote_instance=SimpleNamespace(),
        black_option_layers=layers,
        white_option_layers=layers,
        start_sfen="startpos",
        max_plies=240,
        clear_hash_before_game=False,
        after_variant_setoption="none",
        engine_factory_service=SimpleNamespace(artifact_resolver=None),
        **common,
    )
    remote = GameExecutionSpec.model_validate(remote_payload)

    assert remote.black_engine.usi.go_options == {"nodes": 123}
    assert remote.black_engine.process.handshake_timeout_ms == 12_000
    assert remote.black_engine.variant_id == "plus"
    assert remote.black_engine.usi.clear_hash_before_game is False
    assert remote.black_engine.usi.after_variant_setoption == "none"
    assert local.spec.model_dump(mode="json") == remote.model_dump(mode="json")
    assert local.spec.execution_digest == remote.execution_digest
