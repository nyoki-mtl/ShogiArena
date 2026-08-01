"""Local GameExecutionSpec production resolver tests。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration import local_game_execution_contract
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_manifest import (
    ensure_remote_logical_job_key,
    persist_game_execution_manifest,
    persist_remote_game_assignment,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.local_game_execution_contract import (
    _local_platform,
    resolve_local_game_execution,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def _engine_config(tmp_path: Path, name: str) -> Path:
    binary = tmp_path / name
    binary.write_bytes(name.encode())
    config = tmp_path / f"{name}.yaml"
    config.write_text(
        "\n".join(
            [
                f"name: {name}",
                f'engine_path: "{binary.as_posix()}"',
                f'working_directory: "{tmp_path.as_posix()}"',
                "options:",
                "  Hash: 128",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize(
    ("sys_platform", "machine", "expected_os", "expected_architecture"),
    [
        ("darwin", "arm64", "macos", "arm64"),
        ("darwin", "x86_64", "macos", "x86_64"),
        ("linux", "aarch64", "linux", "arm64"),
        ("win32", "AMD64", "windows", "x86_64"),
    ],
)
def test_local_platform_is_derived_from_os_and_architecture(
    monkeypatch: pytest.MonkeyPatch,
    sys_platform: str,
    machine: str,
    expected_os: str,
    expected_architecture: str,
) -> None:
    monkeypatch.setattr(local_game_execution_contract.sys, "platform", sys_platform)
    monkeypatch.setattr(local_game_execution_contract.platform, "machine", lambda: machine)

    target = _local_platform()

    assert target.operating_system == expected_os
    assert target.architecture == expected_architecture


def test_local_contract_preserves_spsa_variant_and_persists_manifest(tmp_path: Path) -> None:
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
    resolved = resolve_local_game_execution(
        run_id="run",
        game_id="game",
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        black_config_path=_engine_config(tmp_path, "black"),
        white_config_path=_engine_config(tmp_path, "white"),
        black_artifact_overlay_options={},
        white_artifact_overlay_options={},
        black_arena_options={"Hash": 192},
        white_arena_options={"Hash": 384},
        black_overlay_options={"Hash": 224},
        white_overlay_options={"Hash": 448},
        black_inline_options={"Hash": 256},
        white_inline_options={"Hash": 512},
        black_variant_options={"ParamA": 10},
        white_variant_options={"ParamA": -10},
        black_variant_id="plus",
        white_variant_id="minus",
        clear_hash_before_game=True,
        after_variant_setoption="isready",
        black_path_option_names=(),
        white_path_option_names=(),
        black_go_options={"nodes": 123},
        white_go_options={"depth": 7},
        black_handshake_timeout_s=12.0,
        white_handshake_timeout_s=13.0,
        black_limits=limits,
        white_limits=limits,
        rules=rules,
        engine_lifecycle="reuse",
        timeout_reclassification_enabled=True,
    )

    assert resolved.spec.black_engine.usi.static_options == {"Hash": 256}
    assert resolved.spec.black_engine.usi.variant_options == {"ParamA": 10}
    assert resolved.spec.black_engine.variant_id == "plus"
    assert resolved.spec.black_engine.usi.go_options == {"nodes": 123}
    assert resolved.spec.black_engine.process.handshake_timeout_ms == 12_000
    assert resolved.spec.rules.adjudication.resign_threshold_cp == 900
    assert resolved.spec.rules.repetition.occurrences_to_draw == 4
    assert resolved.spec.timeout.reclassification == "invalid_on_coordinator_stall"

    path = persist_game_execution_manifest(
        run_dir=tmp_path / "run",
        game_id="game",
        payload=resolved.manifest_payload(),
    )
    content = path.read_text(encoding="utf-8")
    assert resolved.spec.execution_digest in content
    assert "engine_provenance" in content


def test_remote_assignment_is_persisted_idempotently_and_resume_conflict_fails(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    path = persist_game_execution_manifest(
        run_dir=run_dir,
        game_id="game",
        payload={"game_execution_spec": {"execution_digest": "a" * 64}},
    )
    assignment = {
        "schema_version": "shogiarena.remote-assignment.v1",
        "instance_id": "worker",
        "endpoint_identity": "ssh://worker#key",
        "deployment_id": "b" * 64,
        "job_id": "job-" + "1" * 32,
        "attempt_id": "attempt-" + "2" * 32,
        "execution_digest": "a" * 64,
        "artifact_digests": ["c" * 64],
    }

    persist_remote_game_assignment(run_dir=run_dir, game_id="game", assignment=assignment)
    persist_remote_game_assignment(run_dir=run_dir, game_id="game", assignment=assignment)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["remote_assignment"] == assignment

    persist_game_execution_manifest(
        run_dir=run_dir,
        game_id="game",
        payload={
            "game_execution_spec": {"execution_digest": "a" * 64},
            "engine_provenance": [],
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["remote_assignment"] == assignment

    before_conflict = path.read_bytes()
    with pytest.raises(ValueError, match="conflicts with resumed execution spec"):
        persist_game_execution_manifest(
            run_dir=run_dir,
            game_id="game",
            payload={"game_execution_spec": {"execution_digest": "d" * 64}},
        )
    assert path.read_bytes() == before_conflict

    conflicting = {**assignment, "instance_id": "other-worker"}
    with pytest.raises(ValueError, match="conflicts with resumed dispatch"):
        persist_remote_game_assignment(
            run_dir=run_dir,
            game_id="game",
            assignment=conflicting,
        )


def test_remote_logical_job_key_is_stable_per_run_and_isolates_new_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(["1" * 32, "2" * 32])
    monkeypatch.setattr(
        "shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_manifest.secrets.token_hex",
        lambda _size: next(tokens),
    )
    payload = {
        "game_execution_spec": {
            "execution_digest": "a" * 64,
            "identity": {"job_id": "experiment:game"},
        }
    }
    first_run = tmp_path / "run-1"
    second_run = tmp_path / "run-2"
    first_path = persist_game_execution_manifest(run_dir=first_run, game_id="game", payload=payload)
    persist_game_execution_manifest(run_dir=second_run, game_id="game", payload=payload)

    first_key = ensure_remote_logical_job_key(run_dir=first_run, game_id="game")
    second_key = ensure_remote_logical_job_key(run_dir=second_run, game_id="game")

    assert first_key == f"run-{'1' * 32}:game"
    assert second_key == f"run-{'2' * 32}:game"
    assert first_key != second_key
    assert ensure_remote_logical_job_key(run_dir=first_run, game_id="game") == first_key

    persist_game_execution_manifest(run_dir=first_run, game_id="game", payload=payload)
    persisted = json.loads(first_path.read_text(encoding="utf-8"))
    assert persisted["remote_logical_job_key"] == first_key
