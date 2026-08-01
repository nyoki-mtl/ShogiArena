"""GameExecutionSpec resolver tests (task 0054 Phase 2.2)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_spec_resolver import (
    EngineSpecResolveRequest,
    GameSpecResolveRequest,
    resolve_engine_execution,
    resolve_game_execution,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    ExecutionIdentity,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    RepetitionSpec,
    TargetPlatform,
    TimeoutPolicySpec,
)
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.shared.kernel.content_hashing import sha256_tree


def _write_engine_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    engine_path = tmp_path / "engine"
    engine_path.write_bytes(b"usi-engine-v1")
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    model_path = eval_dir / "model.bin"
    model_path.write_bytes(b"model-v1")
    config_path = tmp_path / "engine.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: FixtureEngine",
                f'engine_path: "{engine_path.as_posix()}"',
                f'working_directory: "{tmp_path.as_posix()}"',
                "engine_args:",
                '  - "--usi"',
                "environment:",
                '  OMP_NUM_THREADS: "1"',
                "secret_environment:",
                '  ENGINE_LICENSE: "SHOGIARENA_SECRET_ENGINE_LICENSE"',
                "handshake_timeout: 12.5",
                "enable_early_ponder: true",
                'isready_sync_strategy: "wait"',
                'isready_lock_template: "{Hash}.lock"',
                "isready_lock_check_templates:",
                '  - "{Hash}.ready"',
                "isready_lock_skip_if_exists: true",
                "mate_default_ply_limit: 7",
                "mate_wait_for_bestmove: true",
                "io:",
                "  collect_info_strings: true",
                "  collect_raw_io: true",
                "  collect_stderr: false",
                "  collect_outbound: true",
                "option_validation:",
                '  default: "warn"',
                "  overrides:",
                '    Hash: "strict"',
                "options:",
                "  Threads: 1",
                "  Hash: 128",
                f'  EvalDir: "{eval_dir.as_posix()}"',
                '  EvalFile: "model.bin"',
                "  ParamA: 0",
                "go_options:",
                "  nodes: 1000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path, engine_path, model_path


def _write_shipped_engine_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    engine_path = tmp_path / "engine"
    engine_path.write_bytes(b"usi-engine-v1")
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    model_path = eval_dir / "model.bin"
    model_path.write_bytes(b"model-v1")
    source = Path("tests/fixtures/0054/full_engine_execution.yaml").read_text(encoding="utf-8")
    config_path = tmp_path / "full-engine.yaml"
    config_path.write_text(
        source.replace("__ENGINE_PATH__", engine_path.as_posix())
        .replace("__WORKING_DIRECTORY__", tmp_path.as_posix())
        .replace("__EVAL_DIRECTORY__", eval_dir.as_posix()),
        encoding="utf-8",
    )
    return config_path, engine_path, model_path


def _request(config_path: Path) -> EngineSpecResolveRequest:
    return EngineSpecResolveRequest(
        config_path=config_path,
        engine_id="fixture",
        artifact_logical_id="fixture-linux",
        target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
        arena_options={"Hash": 256, "MaxMovesToDraw": 320},
        artifact_overlay_options={"Hash": 160},
        overlay_options={"Hash": 384},
        inline_options={"Hash": 512},
        variant_options={"ParamA": 12},
        go_options={"depth": 8},
        path_option_names=("EvalDir",),
        variant_id="plus",
        lifecycle="per_game",
        clear_hash_before_game=True,
        after_variant_setoption="isready",
    )


def test_resolver_loads_yaml_once_and_preserves_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, engine_path, _ = _write_shipped_engine_fixture(tmp_path)
    original_from_file = UsiEngineConfig.from_file
    calls: list[Path] = []

    def _counting_from_file(
        path: str | Path,
        *,
        output_dir: Path | None = None,
        engine_dir: Path | None = None,
    ) -> UsiEngineConfig:
        calls.append(Path(path))
        return original_from_file(path, output_dir=output_dir, engine_dir=engine_dir)

    monkeypatch.setattr(UsiEngineConfig, "from_file", _counting_from_file)

    resolved = resolve_engine_execution(_request(config_path))

    assert calls == [config_path.resolve()]
    assert resolved.spec.process.arguments == ["--usi"]
    assert resolved.spec.process.environment == {"OMP_NUM_THREADS": "2"}
    assert resolved.spec.process.secret_environment_refs == {"ENGINE_LICENSE": "SHOGIARENA_SECRET_ENGINE_LICENSE"}
    assert resolved.spec.process.lifecycle == "per_game"
    assert resolved.spec.process.handshake_timeout_ms == 12_500
    assert resolved.spec.process.collect_info_strings is True
    assert resolved.spec.process.collect_stderr is False
    assert resolved.spec.usi.static_options == {
        "Threads": 2,
        "Hash": 512,
        "MaxMovesToDraw": 320,
    }
    assert resolved.spec.usi.variant_options == {"ParamA": 12}
    assert resolved.spec.usi.go_options == {"nodes": 1000, "depth": 8}
    assert resolved.spec.usi.option_validation == "warn"
    assert resolved.spec.usi.option_validation_overrides == {"Hash": "strict"}
    assert resolved.spec.usi.early_ponder is True
    assert resolved.spec.usi.isready_sync_strategy == "wait"
    assert resolved.spec.usi.isready_lock_key == "512.lock"
    assert resolved.spec.usi.isready_lock_check_keys == ["512.ready"]
    assert resolved.spec.usi.skip_isready_lock_if_exists is True
    assert resolved.spec.usi.mate_default_ply_limit == 7
    assert resolved.spec.usi.mate_wait_for_bestmove is True
    assert resolved.provenance.engine_sha256 == hashlib.sha256(engine_path.read_bytes()).hexdigest()


def test_resolver_converts_composite_path_to_content_addressed_reference(tmp_path: Path) -> None:
    config_path, _, model_path = _write_engine_fixture(tmp_path)

    resolved = resolve_engine_execution(_request(config_path))

    assert len(resolved.spec.usi.path_resources) == 1
    resource = resolved.spec.usi.path_resources[0]
    expected_digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    assert resource.artifact.kind == "file"
    assert resource.artifact.sha256 == expected_digest
    assert resource.target_relative_path == f"resources/sha256/{expected_digest[:2]}/{expected_digest}/model.bin"
    assert resource.option_values == {
        "EvalDir": f"resources/sha256/{expected_digest[:2]}/{expected_digest}",
        "EvalFile": "model.bin",
    }
    assert "EvalDir" not in resolved.spec.usi.static_options
    assert "EvalFile" not in resolved.spec.usi.static_options


def test_resolver_preserves_disabled_book_sentinel_as_static_option(tmp_path: Path) -> None:
    config_path, _, _ = _write_engine_fixture(tmp_path)
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text.replace(
            "go_options:\n",
            "  BookFile: no_book\ngo_options:\n",
        ),
        encoding="utf-8",
    )

    resolved = resolve_engine_execution(_request(config_path))

    assert resolved.spec.usi.static_options["BookFile"] == "no_book"
    assert all("BookFile" not in resource.option_values for resource in resolved.spec.usi.path_resources)
    assert all(not path.endswith("no_book") for path in resolved.provenance.path_sources)


def test_resolver_directory_resource_digest_is_stable(tmp_path: Path) -> None:
    config_path, _, _ = _write_engine_fixture(tmp_path)
    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "a.db").write_bytes(b"a")
    (book_dir / "b.db").write_bytes(b"b")
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text.replace(
            "go_options:\n",
            f'  SyzygyPath: "{book_dir.as_posix()}"\ngo_options:\n',
        ),
        encoding="utf-8",
    )

    request = replace(_request(config_path), path_option_names=("EvalDir", "SyzygyPath"))

    resolved = resolve_engine_execution(request)
    book_resource = next(
        resource for resource in resolved.spec.usi.path_resources if "SyzygyPath" in resource.option_values
    )
    assert book_resource.artifact.kind == "directory"
    assert book_resource.artifact.sha256 == sha256_tree(book_dir)


def test_resolver_rejects_missing_engine_binary(tmp_path: Path) -> None:
    config_path = tmp_path / "engine.yaml"
    config_path.write_text(
        'name: Missing\nengine_path: "missing-engine"\n',
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="Resolved engine binary not found"):
        resolve_engine_execution(_request(config_path))


def test_resolver_hashes_explicitly_resolved_engine_artifact(tmp_path: Path) -> None:
    config_path, _, _ = _write_engine_fixture(tmp_path)
    target_binary = tmp_path / "engine-linux-avx2"
    target_binary.write_bytes(b"resolved-for-remote-target")

    resolved = resolve_engine_execution(replace(_request(config_path), engine_source_override=target_binary))

    assert resolved.provenance.engine_source_path == str(target_binary.resolve())
    assert resolved.spec.process.artifact.sha256 == hashlib.sha256(target_binary.read_bytes()).hexdigest()
    assert resolved.spec.process.artifact.entrypoint == target_binary.name


def test_resolver_rejects_windows_binary_for_linux_target(tmp_path: Path) -> None:
    config_path, _, _ = _write_engine_fixture(tmp_path)
    windows_binary = tmp_path / "engine.exe"
    windows_binary.write_bytes(b"windows-pe")

    with pytest.raises(ValueError, match="Windows engine binary cannot target Linux worker"):
        resolve_engine_execution(replace(_request(config_path), engine_source_override=windows_binary))


def test_full_resolver_seals_transport_neutral_spec_and_manifest_provenance(tmp_path: Path) -> None:
    config_path, _, _ = _write_engine_fixture(tmp_path)
    black = replace(
        _request(config_path),
        engine_id="black",
        artifact_logical_id="black-linux",
        variant_id="plus",
    )
    white = replace(
        _request(config_path),
        engine_id="white",
        artifact_logical_id="white-linux",
        variant_id="minus",
    )
    time_control = GameTimeControlSpec(fixed_time_ms=100)

    resolved = resolve_game_execution(
        GameSpecResolveRequest(
            minimum_worker_version="1.1.0",
            identity=ExecutionIdentity(
                job_id="job-1",
                run_id="run-1",
                game_id="game-1",
                update_idx=0,
                pair_id="pair-1",
            ),
            black_engine=black,
            white_engine=white,
            opening=OpeningSpec(
                initial_sfen="startpos",
                black_engine_id="black",
                white_engine_id="white",
            ),
            time=GameTimeSpec(
                black=time_control,
                white=time_control,
                startup_grace_ms=30_000,
                outer_deadline_ms=60_000,
            ),
            rules=GameRulesSpec(
                adjudication=AdjudicationSpec(),
                repetition=RepetitionSpec(),
            ),
            timeout=TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification="invalid_on_coordinator_stall",
            ),
            required_tags=("cpu",),
            output=OutputContract(required_provenance=["engine_artifacts", "effective_options"]),
        )
    )

    manifest = resolved.manifest_payload()
    assert resolved.spec.execution_digest
    assert resolved.spec.resources.artifact_ids == sorted(resolved.spec.resources.artifact_ids)
    assert manifest["game_execution_spec"] == resolved.spec.model_dump(mode="json")
    assert manifest["engine_provenance"] == [
        resolved.engine_provenance[0].to_payload(),
        resolved.engine_provenance[1].to_payload(),
    ]
    spec_payload = resolved.spec.model_dump(mode="json")
    assert "execution_mode" not in spec_payload
    assert "instance_id" not in spec_payload
