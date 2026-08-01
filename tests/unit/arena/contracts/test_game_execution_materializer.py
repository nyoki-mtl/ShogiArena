"""GameExecutionSpec runtime materialization tests (task 0054 Phase 2)."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import (
    _engine_pool_contract_digest,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    materialize_engine_config,
    materialize_opening_sfen,
    resolve_effective_handshake_timeout,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    OpeningSpec,
    PathResourceRef,
    PlatformArtifactRef,
    TargetPlatform,
)
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256


def _engine() -> EngineExecutionSpec:
    platform = TargetPlatform(operating_system="linux", architecture="x86_64")
    return EngineExecutionSpec(
        engine_id="engine-a",
        process=EngineProcessSpec(
            artifact=PlatformArtifactRef(
                logical_id="engine-a-linux",
                kind="engine_binary",
                sha256="a" * 64,
                target_platform=platform,
                entrypoint="engine",
            ),
            arguments=["--usi"],
            working_directory="engines/engine-a-linux",
            environment={"PUBLIC_VALUE": "visible"},
            secret_environment_refs={"ENGINE_TOKEN": "SHOGIARENA_SECRET_ENGINE_TOKEN"},
            handshake_timeout_ms=5_000,
        ),
        usi=EngineUsiSpec(
            static_options={"Threads": 2},
            path_resources=[
                PathResourceRef(
                    option_values={
                        "BookDir": "resources/sha256/ab/digest",
                        "BookFile": "book.db",
                    },
                    artifact=PlatformArtifactRef(
                        logical_id="book-ab",
                        kind="file",
                        sha256="b" * 64,
                        target_platform=platform,
                    ),
                    target_relative_path="resources/sha256/ab/digest/book.db",
                )
            ],
        ),
    )


def test_local_and_remote_materializers_consume_same_sealed_engine_contract(tmp_path: Path) -> None:
    engine = _engine()
    digest = canonical_sha256(engine.model_dump(mode="json"))
    local_engine = tmp_path / "local" / "engine"
    local_book = tmp_path / "local" / "book.db"
    local_mapping = materialize_engine_config(
        engine,
        execution_root=tmp_path,
        artifact_paths={"engine-a-linux": local_engine, "book-ab": local_book},
        secret_values={"SHOGIARENA_SECRET_ENGINE_TOKEN": "secret"},
    )
    remote_mapping = materialize_engine_config(
        engine,
        execution_root=Path("/worker"),
        secret_values={"SHOGIARENA_SECRET_ENGINE_TOKEN": "secret"},
    )

    assert canonical_sha256(engine.model_dump(mode="json")) == digest
    assert local_mapping["engine_args"] == remote_mapping["engine_args"] == ["--usi"]
    assert local_mapping["environment"] == remote_mapping["environment"]
    assert local_mapping["engine_path"] == str(local_engine.resolve())
    assert remote_mapping["engine_path"] == str(Path("/worker/engines/engine-a-linux/engine"))


def test_materializer_inherits_only_declared_secret_references(tmp_path: Path) -> None:
    mapping = materialize_engine_config(
        _engine(),
        execution_root=tmp_path,
        secret_values={
            "SHOGIARENA_SECRET_ENGINE_TOKEN": "declared",
            "UNRELATED_SECRET": "must-not-leak",
        },
    )

    assert mapping["environment"] == {
        "PUBLIC_VALUE": "visible",
        "ENGINE_TOKEN": "declared",
    }


def test_engine_pool_contract_excludes_per_game_variant_but_tracks_startup_contract(tmp_path: Path) -> None:
    base = _engine()
    variant = base.model_copy(
        update={
            "variant_id": "plus",
            "usi": base.usi.model_copy(
                update={
                    "variant_options": {"ParamA": 12},
                    "clear_hash_before_game": True,
                    "after_variant_setoption": "isready",
                }
            ),
        }
    )
    changed_startup = base.model_copy(
        update={
            "usi": base.usi.model_copy(update={"static_options": {"Threads": 4}}),
        }
    )

    def materialize(engine: EngineExecutionSpec, *, secret: str = "secret") -> dict[str, object]:
        return materialize_engine_config(
            engine,
            execution_root=tmp_path,
            secret_values={"SHOGIARENA_SECRET_ENGINE_TOKEN": secret},
        )

    base_digest = _engine_pool_contract_digest(materialize(base))

    assert _engine_pool_contract_digest(materialize(variant)) == base_digest
    assert _engine_pool_contract_digest(materialize(changed_startup)) != base_digest
    assert _engine_pool_contract_digest(materialize(base, secret="rotated")) != base_digest


def test_opening_moves_are_applied_to_initial_sfen() -> None:
    opening = OpeningSpec(
        initial_sfen="startpos",
        moves_usi=["7g7f", "3c3d"],
        black_engine_id="engine-a",
        white_engine_id="engine-b",
    )

    assert materialize_opening_sfen(opening).endswith(" b - 3")


def test_effective_handshake_timeout_precedence_and_common_default() -> None:
    assert resolve_effective_handshake_timeout(12.0, 30.0) == 12.0
    assert resolve_effective_handshake_timeout(None, 30.0) == 30.0
    assert resolve_effective_handshake_timeout(None, None) == 120.0
