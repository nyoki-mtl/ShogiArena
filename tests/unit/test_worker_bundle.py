from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.application import worker_bundle_builder as worker_bundle


def test_worker_bundle_is_deterministic_without_git_or_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        '[project]\nname = "shogiarena"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    def fake_run(command: list[str], *, environment: dict[str, str]) -> str:
        assert environment["SOURCE_DATE_EPOCH"] == "315532800"
        if command[1] == "build":
            output_dir = Path(command[command.index("--out-dir") + 1])
            (output_dir / "shogiarena-1.2.3-py3-none-any.whl").write_bytes(b"fixed-wheel")
            return ""
        if command[1] == "export":
            output_file = Path(command[command.index("--output-file") + 1])
            output_file.write_text("pydantic==2.13.4\n", encoding="utf-8")
            return ""
        assert command == ["uv", "--version"]
        return "uv 0.11.29\n"

    monkeypatch.setattr(worker_bundle, "_run_checked", fake_run)
    first = worker_bundle.build_worker_bundle(
        output_path=(tmp_path / "first.zip").resolve(),
        project_root=project_root,
    )
    second = worker_bundle.build_worker_bundle(
        output_path=(tmp_path / "second.zip").resolve(),
        project_root=project_root,
    )

    assert first.manifest.deployment_id == second.manifest.deployment_id
    assert first.bundle_sha256 == second.bundle_sha256
    assert first.manifest.source_policy == "snapshot"
    assert first.manifest.protocol_version == "shogiarena.remote-worker.v1"
    assert first.manifest.lock_sha256
    assert first.manifest.manifest_sha256
    assert worker_bundle.read_worker_bundle_manifest(first.bundle_path) == first.manifest
    with zipfile.ZipFile(first.bundle_path) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "requirements.lock",
            "worker/shogiarena-1.2.3-py3-none-any.whl",
        ]


def test_worker_bundle_requires_absolute_output(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="absolute"):
        worker_bundle.build_worker_bundle(output_path=Path("worker.zip"), project_root=project_root)


def test_worker_bundle_stages_an_installed_distribution_without_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer_root = tmp_path / "consumer"
    (consumer_root / ".venv" / "Lib" / "site-packages").mkdir(parents=True)
    (consumer_root / "pyproject.toml").write_text(
        '[project]\nname = "consumer-app"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    (consumer_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    package_root = consumer_root / ".venv" / "Lib" / "site-packages" / "shogiarena"
    builder_path = package_root / "_core" / "contexts" / "game_session" / "application" / "worker_bundle_builder.py"
    builder_path.parent.mkdir(parents=True)
    builder_path.write_text("# installed package marker\n", encoding="utf-8")
    (package_root / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    resources = package_root / "_worker_bundle_resources"
    resources.mkdir()
    (resources / "pyproject.toml").write_text(
        '[project]\nname = "shogiarena"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (resources / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (resources / "README.md").write_text("# ShogiArena\n", encoding="utf-8")
    monkeypatch.setattr(worker_bundle, "__file__", str(builder_path))

    assert worker_bundle._find_worker_project_root() is None

    def fake_run(command: list[str], *, environment: dict[str, str]) -> str:
        del environment
        if command[1] == "build":
            staged_root = Path(command[-1])
            assert (staged_root / "src" / "shogiarena" / "__init__.py").is_file()
            assert not (staged_root / "src" / "shogiarena" / "_worker_bundle_resources").exists()
            output_dir = Path(command[command.index("--out-dir") + 1])
            (output_dir / "shogiarena-1.2.3-py3-none-any.whl").write_bytes(b"installed-wheel")
            return ""
        if command[1] == "export":
            output_file = Path(command[command.index("--output-file") + 1])
            output_file.write_text("pydantic==2.13.4\n", encoding="utf-8")
            return ""
        return "uv 0.11.29\n"

    monkeypatch.setattr(worker_bundle, "_run_checked", fake_run)

    result = worker_bundle.build_worker_bundle(output_path=(tmp_path / "worker.zip").resolve())

    assert result.manifest.package_version == "1.2.3"
    assert result.bundle_path.is_file()
