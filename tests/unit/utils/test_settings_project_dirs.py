from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.interfaces.cli.main import _configure_runtime_settings, build_parser
from shogiarena._core.platform.settings import facade as settings_mod
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.settings_loading.settings_models import ArenaSettings, RepoSettings


def _snapshot_project_dirs_state() -> dict:
    return {
        "output_dir": project_dirs.output_dir,
        "engine_dir": project_dirs.engine_dir,
        "repos": dict(project_dirs.repos),
        "overlays": dict(project_dirs.overlays),
        "settings_path": project_dirs.settings_path,
        "log_root_dir": project_dirs.log_root_dir,
    }


def _snapshot_settings_state() -> ArenaSettings:
    return settings_mod.SETTINGS


def _snapshot_instance_pool_state() -> Path:
    return InstancePool.DEFAULT_LOCAL_INSTANCES_PATH


def _restore_state(
    dirs_snapshot: dict,
    settings_snapshot: ArenaSettings,
    instance_pool_snapshot: Path,
) -> None:
    project_dirs.output_dir = dirs_snapshot["output_dir"]
    project_dirs.engine_dir = dirs_snapshot["engine_dir"]
    project_dirs.repos = dirs_snapshot["repos"]
    project_dirs.overlays = dirs_snapshot["overlays"]
    project_dirs.settings_path = dirs_snapshot["settings_path"]
    project_dirs.log_root_dir = dirs_snapshot["log_root_dir"]
    settings_mod.SETTINGS = settings_snapshot
    InstancePool.DEFAULT_LOCAL_INSTANCES_PATH = instance_pool_snapshot


def test_apply_settings_syncs_project_dirs_from_arena_settings() -> None:
    dirs_snapshot = _snapshot_project_dirs_state()
    settings_snapshot = _snapshot_settings_state()
    instance_pool_snapshot = _snapshot_instance_pool_state()
    try:
        configured = ArenaSettings(
            output_dir=Path("/tmp/shogiarena-output"),
            engine_dir=Path("/tmp/shogiarena-engine"),
            settings_path=Path("/tmp/settings.yaml"),
            repos={
                "local": RepoSettings(name="local", path=Path("/tmp/repo")),
            },
            github_token="token",
            overlays={"overlay": Path("/tmp/overlay.yaml")},
            openbench=None,
        )

        project_dirs._apply_settings(configured)

        assert project_dirs.output_dir == configured.output_dir
        assert project_dirs.engine_dir == configured.engine_dir
        assert project_dirs.repos == configured.repos
        assert project_dirs.overlays == configured.overlays
        assert project_dirs.settings_path == configured.settings_path
        assert project_dirs.log_root_dir == configured.output_dir / "logs"
    finally:
        _restore_state(dirs_snapshot, settings_snapshot, instance_pool_snapshot)


def test_configure_settings_updates_project_dirs_from_settings_file(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.yaml"
    expected_output = tmp_path / "dashboard"
    expected_engine = tmp_path / "engines"
    settings_path.write_text(
        f"""
output_dir: {expected_output}
engine_dir: {expected_engine}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(settings_mod, "default_settings_path", lambda: settings_path)

    dirs_snapshot = _snapshot_project_dirs_state()
    settings_snapshot = _snapshot_settings_state()
    instance_pool_snapshot = _snapshot_instance_pool_state()
    try:
        configured = settings_mod.configure_settings(should_require_settings=True, should_suppress_warning=True)

        assert configured.output_dir == expected_output
        assert configured.engine_dir == expected_engine
        assert project_dirs.output_dir == expected_output
        assert project_dirs.engine_dir == expected_engine
        assert project_dirs.settings_path == settings_path
        assert project_dirs.log_root_dir == expected_output / "logs"
    finally:
        _restore_state(dirs_snapshot, settings_snapshot, instance_pool_snapshot)


def test_configure_settings_updates_instance_pool_default_local_path_after_parser_import(
    tmp_path,
    monkeypatch,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    expected_output = tmp_path / "override-output"
    expected_engine = tmp_path / "engines"
    settings_path.write_text(
        f"""
output_dir: {expected_output}
engine_dir: {expected_engine}
""",
        encoding="utf-8",
    )

    build_parser()
    monkeypatch.setattr(settings_mod, "default_settings_path", lambda: settings_path)

    dirs_snapshot = _snapshot_project_dirs_state()
    settings_snapshot = _snapshot_settings_state()
    instance_pool_snapshot = _snapshot_instance_pool_state()
    try:
        _configure_runtime_settings(
            output_dir=None,
            should_require_settings=True,
            should_suppress_warning=True,
        )

        assert InstancePool.DEFAULT_LOCAL_INSTANCES_PATH == expected_output / "instances" / "local.yaml"
    finally:
        _restore_state(dirs_snapshot, settings_snapshot, instance_pool_snapshot)
