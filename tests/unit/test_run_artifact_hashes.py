from __future__ import annotations

import json

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration import config_tournament
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    resolve_engine_config_entry,
)
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.run_artifact_contract import (
    build_engine_manifest_payload,
    build_run_artifact_payload_bundle,
    build_schedule_payload,
)
from shogiarena._core.shared.kernel.run_artifact_hashes import (
    ResumeHashRequest,
    RunArtifactHashRequest,
    build_run_artifact_hash_bundle,
    build_sprt_test_definition,
    canonical_json_bytes,
    config_fingerprint,
    provenance_hash,
    resume_hash,
    schedule_hash,
    sprt_test_def_hash,
)


def test_canonical_json_bytes_are_stable_for_key_order() -> None:
    left = {"b": 2, "a": {"z": 1, "y": [3, 2, 1]}}
    right = {"a": {"y": [3, 2, 1], "z": 1}, "b": 2}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert json.loads(canonical_json_bytes(left).decode("utf-8")) == right


def test_hash_kinds_are_domain_separated() -> None:
    payload = {"same": "payload"}

    assert config_fingerprint(payload) != schedule_hash(payload)
    assert schedule_hash(payload) != provenance_hash(payload)


def test_sprt_model_changes_resume_hash_but_not_schedule_hash() -> None:
    def _sprt_payload(model: str) -> dict[str, JsonValue]:
        return {"model": model, "elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05}

    def _config(sprt_payload: dict[str, JsonValue]) -> dict[str, object]:
        return {
            "tournament": {"format": "round_robin", "games_per_pair": 2},
            "sprt": sprt_payload,
            "engines": [{"name": "a"}, {"name": "b"}],
        }

    tri_sprt = _sprt_payload("gsprt-trinomial-v1")
    pen_sprt = _sprt_payload("gsprt-pentanomial-v1")

    # The schedule (game plan) must be identical regardless of the analysis/stopping model.
    assert build_schedule_payload(_config(tri_sprt)) == build_schedule_payload(_config(pen_sprt))
    assert schedule_hash(build_schedule_payload(_config(tri_sprt))) == schedule_hash(
        build_schedule_payload(_config(pen_sprt))
    )

    # The resume test definition is model-sensitive, so cross-model resume is rejected.
    assert sprt_test_def_hash(tri_sprt) != sprt_test_def_hash(pen_sprt)


def test_schedule_hash_includes_effective_options_but_not_provenance_bytes() -> None:
    base_schedule = {
        "kind": "tournament",
        "games": [{"game_id": "g1", "black": "a", "white": "b"}],
        "effective_options": {"a": {"Threads": 4}},
    }
    changed_options = {
        "kind": "tournament",
        "games": [{"game_id": "g1", "black": "a", "white": "b"}],
        "effective_options": {"a": {"Threads": 8}},
    }
    changed_bytes_only = {
        "kind": "tournament",
        "games": [{"game_id": "g1", "black": "a", "white": "b"}],
        "effective_options": {"a": {"Threads": 4}},
    }

    assert schedule_hash(base_schedule) != schedule_hash(changed_options)
    assert schedule_hash(base_schedule) == schedule_hash(changed_bytes_only)


def test_provenance_hash_changes_for_physical_bytes() -> None:
    base = {"engines": [{"name": "a", "binary_sha256": "0" * 64}]}
    changed = {"engines": [{"name": "a", "binary_sha256": "1" * 64}]}

    assert provenance_hash(base) != provenance_hash(changed)


def test_sprt_test_definition_excludes_budget_fields() -> None:
    base = {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05, "max_games": 400}
    extended = {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05, "max_games": 800}
    changed_boundary = {"elo0": 0.0, "elo1": 10.0, "alpha": 0.05, "beta": 0.05, "max_games": 400}

    assert build_sprt_test_definition(base) == {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05}
    assert sprt_test_def_hash(base) == sprt_test_def_hash(extended)
    assert sprt_test_def_hash(base) != sprt_test_def_hash(changed_boundary)


def test_resume_hash_includes_resume_contract_version_and_sprt_definition() -> None:
    schedule_digest = schedule_hash({"kind": "tournament", "games": []})
    provenance_digest = provenance_hash({"engines": []})
    sprt_digest = sprt_test_def_hash({"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05})

    base = resume_hash(
        ResumeHashRequest(
            schedule_hash=schedule_digest,
            provenance_hash=provenance_digest,
            sprt_test_def_hash=sprt_digest,
        )
    )
    changed_contract = resume_hash(
        ResumeHashRequest(
            schedule_hash=schedule_digest,
            provenance_hash=provenance_digest,
            sprt_test_def_hash=sprt_digest,
            resume_contract_version=2,
        )
    )
    changed_sprt = resume_hash(
        ResumeHashRequest(
            schedule_hash=schedule_digest,
            provenance_hash=provenance_digest,
            sprt_test_def_hash=sprt_test_def_hash({"elo0": 0.0, "elo1": 10.0, "alpha": 0.05, "beta": 0.05}),
        )
    )

    assert base != changed_contract
    assert base != changed_sprt


def test_resume_hash_requires_sealed_provenance() -> None:
    bundle = build_run_artifact_hash_bundle(
        request=RunArtifactHashRequest(
            config_payload={"experiment_name": "demo"},
            schedule_payload={"kind": "tournament", "games": []},
            provenance_payload=None,
        )
    )

    assert bundle.provenance_hash is None
    assert bundle.resume_hash is None


def test_resume_hash_request_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="schedule_hash"):
        resume_hash(ResumeHashRequest(schedule_hash="", provenance_hash="abc"))
    with pytest.raises(ValueError, match="provenance_hash"):
        resume_hash(ResumeHashRequest(schedule_hash="abc", provenance_hash=""))


def test_run_artifact_schedule_hash_ignores_runtime_parallelism() -> None:
    base = {
        "experiment_name": "demo",
        "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "num_parallel": 1, "seed": 1},
        "rules": {"time_control": {"byoyomi": 1000}},
        "engines": [{"name": "a", "engine_path": "/missing/a"}, {"name": "b", "engine_path": "/missing/b"}],
    }
    changed = {
        **base,
        "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "num_parallel": 8, "seed": 1},
    }

    assert (
        build_run_artifact_payload_bundle(base).hashes.schedule_hash
        == build_run_artifact_payload_bundle(changed).hashes.schedule_hash
    )


def test_run_artifact_schedule_hash_includes_time_control() -> None:
    base = {
        "experiment_name": "demo",
        "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "seed": 1},
        "rules": {"time_control": {"byoyomi": 1000}},
        "engines": [{"name": "a", "engine_path": "/missing/a"}, {"name": "b", "engine_path": "/missing/b"}],
    }
    changed = {
        **base,
        "rules": {"time_control": {"byoyomi": 2000}},
    }

    assert (
        build_run_artifact_payload_bundle(base).hashes.schedule_hash
        != build_run_artifact_payload_bundle(changed).hashes.schedule_hash
    )


def test_run_artifact_resume_hash_changes_for_binary_bytes(tmp_path) -> None:
    engine_a = tmp_path / "a"
    engine_b = tmp_path / "b"
    engine_a.write_text("a-v1", encoding="utf-8")
    engine_b.write_text("b-v1", encoding="utf-8")
    payload = {
        "experiment_name": "demo",
        "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "seed": 1},
        "engines": [
            {"name": "a", "engine_path": str(engine_a)},
            {"name": "b", "engine_path": str(engine_b)},
        ],
    }
    before = build_run_artifact_payload_bundle(payload).hashes
    engine_a.write_text("a-v2", encoding="utf-8")
    after = build_run_artifact_payload_bundle(payload).hashes

    assert before.schedule_hash == after.schedule_hash
    assert before.resume_hash != after.resume_hash


def test_run_artifact_provenance_hashes_binary_from_engine_yaml(tmp_path) -> None:
    binary_path = tmp_path / "engine.bin"
    engine_yaml = tmp_path / "engine.yaml"
    binary_path.write_bytes(b"engine-v1")
    engine_yaml.write_text(
        f"name: engine-a\nengine_path: {binary_path}\nworking_directory: {tmp_path}\n",
        encoding="utf-8",
    )
    payload = {
        "experiment_name": "demo",
        "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "seed": 1},
        "engines": [{"name": "engine-a", "engine_path": str(engine_yaml)}],
    }

    before = build_run_artifact_payload_bundle(payload).hashes
    manifest_engine = build_engine_manifest_payload(payload["engines"][0])
    binary_path.write_bytes(b"engine-v2")
    after = build_run_artifact_payload_bundle(payload).hashes

    assert manifest_engine["resolved_paths"]["engine_path"] == str(binary_path)
    assert manifest_engine["resolved_paths"]["engine_config"] == str(engine_yaml)
    assert before.schedule_hash == after.schedule_hash
    assert before.resume_hash != after.resume_hash


def test_artifact_engine_materialization_seals_resolved_binary(tmp_path) -> None:
    binary_path = tmp_path / "built-engine"
    binary_path.write_bytes(b"engine")
    engine = EngineConfig(name="engine-a", artifact="repo/abcdef", build_options={"target_cpu": "x86-64"})

    resolved = resolve_engine_config_entry(
        engine,
        output_dir=tmp_path / "inputs" / "engine_configs",
        extra_options=None,
        artifact_resolver=lambda artifact, build_options: binary_path,
    )

    assert resolved.engine_path is not None
    text = resolved.engine_path.read_text(encoding="utf-8")
    assert f"engine_path: {binary_path}" in text
    manifest_engine = build_engine_manifest_payload(resolved.model_dump(mode="json"))
    assert manifest_engine["resolved_paths"]["engine_path"] == str(binary_path)
    assert manifest_engine["bytes_hash"]["engine_binary_sha256"]


def test_sprt_budget_does_not_split_schedule_group() -> None:
    base = {
        "experiment_name": "sprt",
        "tournament": {"scheduler": "round_robin", "games_per_pair": 400, "seed": 1},
        "sprt": {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05, "max_games": 400},
        "engines": [{"name": "a", "engine_path": "/missing/a"}, {"name": "b", "engine_path": "/missing/b"}],
    }
    extended = {
        **base,
        "tournament": {"scheduler": "round_robin", "games_per_pair": 800, "seed": 1},
        "sprt": {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05, "max_games": 800},
    }

    assert (
        build_run_artifact_payload_bundle(base).hashes.schedule_hash
        == build_run_artifact_payload_bundle(extended).hashes.schedule_hash
    )


def test_tournament_config_hash_accessors_are_memoized(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    engine_a = tmp_path / "engine-a.yaml"
    engine_b = tmp_path / "engine-b.yaml"
    engine_a.write_text("engine_path: /bin/echo\n", encoding="utf-8")
    engine_b.write_text("engine_path: /bin/echo\n", encoding="utf-8")
    config = TournamentRunConfig.from_mapping(
        {
            "experiment_name": "demo",
            "engines": [
                {"name": "a", "engine_path": str(engine_a)},
                {"name": "b", "engine_path": str(engine_b)},
            ],
            "tournament": {"games_per_pair": 2, "seed": 1},
        },
        base_dir=tmp_path,
    )
    real_builder = config_tournament.build_run_artifact_payload_bundle
    calls = 0

    def _counting_builder(payload):
        nonlocal calls
        calls += 1
        return real_builder(payload)

    monkeypatch.setattr(config_tournament, "build_run_artifact_payload_bundle", _counting_builder)

    assert config.get_schedule_hash()
    assert calls == 0
    first_resume = config.get_resume_hash()
    second_resume = config.get_resume_hash()
    assert first_resume == second_resume
    assert calls == 1
    config.clear_run_artifact_hash_cache()
    assert config.get_resume_hash() == first_resume
    assert calls == 2
