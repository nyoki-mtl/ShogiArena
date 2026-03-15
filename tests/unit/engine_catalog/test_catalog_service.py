from __future__ import annotations

from pathlib import Path

import yaml

from shogiarena._core.contexts.engine_catalog.adapters.yaml_file_loader import YamlFileLoaderAdapter
from shogiarena._core.contexts.engine_catalog.application.catalog_service import EngineCatalogService
from shogiarena._core.contexts.engine_catalog.ports.catalog_service import (
    EngineCatalogEntryRequest,
    EngineCatalogRequest,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def test_build_metadata_materializes_tournament_style_engine_catalog_request(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config_source_path = tmp_path / "arena.yaml"
    config_source_path.write_text("experiment_name: test\n", encoding="utf-8")

    engine_config_path = tmp_path / "engine.yaml"
    engine_config_path.write_text(
        yaml.safe_dump(
            {
                "artifact": "repo/abcdef12",
                "build_options": {"target": "avx2"},
                "options": {
                    "USI_Ponder": False,
                    "Hash": 16,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    artifact_overlay_path = tmp_path / "artifact_overlay.yaml"
    artifact_overlay_path.write_text(
        yaml.safe_dump({"options": {"Ponder": True}}, sort_keys=False),
        encoding="utf-8",
    )
    options_overlay_path = tmp_path / "overlay.yaml"
    options_overlay_path.write_text(
        yaml.safe_dump({"options": {"BookDir": "{output_dir}/book.bin"}}, sort_keys=False),
        encoding="utf-8",
    )

    def artifact_resolver(artifact: str, build_options: dict[str, object]) -> Path:
        return tmp_path / f"{artifact.replace('/', '_')}_{build_options['target']}"

    request = EngineCatalogRequest(
        entries=(
            EngineCatalogEntryRequest(
                name="engine-a",
                engine_config_path=engine_config_path,
                artifact_overlay_path=artifact_overlay_path,
                inline_options={
                    "Hash": 256,
                    "EvalDir": "{output_dir}/net.nnue",
                },
                options_overlay_paths=(options_overlay_path,),
            ),
        ),
        base_time_control=None,
        run_dir=run_dir,
        path_output_dir=tmp_path / "output",
        engine_dir=tmp_path / "engines",
        config_source_path=config_source_path,
        rules_synced_options={"Draw_Ply|MaxMovesToDraw": 320},
        runtime_options={"engine-a": {"Hash": {"current": 256}}},
        runtime_info={"engine-a": {"author": "tester"}},
    )
    service = EngineCatalogService(
        file_loader=YamlFileLoaderAdapter(),
        artifact_resolver=artifact_resolver,
    )

    metadata = service.build_metadata(request)

    assert len(metadata) == 1
    entry = metadata[0]
    assert entry["name"] == "engine-a"
    assert entry["engine_config_path"] == str(engine_config_path)
    assert entry["engine_path"] == str(tmp_path / "repo_abcdef12_avx2")
    assert entry["config_options"] == {"USI_Ponder": False, "Hash": 16}
    assert entry["extra_options"] == {"Hash": 256, "EvalDir": "{output_dir}/net.nnue"}
    assert entry["overlay_options"] == {"Ponder": True, "BookDir": "{output_dir}/book.bin"}
    assert entry["merged_options"] == {
        "USI_Ponder": False,
        "Hash": 256,
        "Ponder": True,
        "Draw_Ply|MaxMovesToDraw": 320,
        "BookDir": "{output_dir}/book.bin",
        "EvalDir": "{output_dir}/net.nnue",
    }
    assert entry["option_sources"] == {
        "USI_Ponder": "config",
        "Hash": "override",
        "Ponder": "overlay",
        "Draw_Ply|MaxMovesToDraw": "rules",
        "BookDir": "overlay",
        "EvalDir": "override",
    }
    source_details = entry["option_sources_details"]
    assert source_details["USI_Ponder"].startswith("engine config")
    assert source_details["Ponder"].startswith("artifact overlay")
    assert source_details["Draw_Ply|MaxMovesToDraw"].startswith("rules.adjudication")
    assert source_details["BookDir"].startswith("options overlays")
    assert source_details["Hash"].startswith("engines[].options")
    assert entry["resolved_options"]["BookDir"] == str(run_dir / "book.bin")
    assert entry["resolved_options"]["EvalDir"] == str(run_dir / "net.nnue")
    assert entry["runtime_usi_options"] == {"Hash": {"current": 256}}
    assert entry["runtime_engine_info"] == {"author": "tester"}


def test_engine_catalog_service_uses_first_entry_for_duplicate_names(tmp_path: Path) -> None:
    base_time_control = TimeControlLimits(time_ms=10_000)

    def artifact_resolver(artifact: str, build_options: dict[str, object]) -> Path:
        return tmp_path / f"{artifact.replace('/', '_')}_{build_options['target']}"

    request = EngineCatalogRequest(
        entries=(
            EngineCatalogEntryRequest(
                name="dup",
                artifact="repo/first",
                build_options={"target": "one"},
                instance_id=None,
                time_control=None,
            ),
            EngineCatalogEntryRequest(
                name="dup",
                artifact="repo/second",
                build_options={"target": "two"},
                instance_id="remote-1",
                time_control=TimeControlLimits(time_ms=20_000),
            ),
        ),
        base_time_control=base_time_control,
        run_dir=tmp_path,
        path_output_dir=tmp_path / "output",
        engine_dir=tmp_path / "engines",
    )
    service = EngineCatalogService(
        file_loader=YamlFileLoaderAdapter(),
        artifact_resolver=artifact_resolver,
    )

    metadata = service.build_metadata(request)
    time_controls, default_time_control = service.compute_time_control_specs(request)
    instance_defaults = service.compute_instance_defaults(request)

    assert len(metadata) == 1
    assert metadata[0]["engine_path"] == str(tmp_path / "repo_first_one")
    assert time_controls == {"dup": base_time_control.to_spec_str()}
    assert default_time_control == base_time_control.to_spec_str()
    assert instance_defaults == {"dup": "local"}
