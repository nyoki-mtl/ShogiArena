"""Content-addressed remote worker bundle builder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import Literal

from shogiarena._core.contexts.game_session.ports.game_execution_spec import GAME_EXECUTION_PROTOCOL_VERSION
from shogiarena._core.contexts.game_session.ports.worker_deployment import (
    WORKER_BUNDLE_SCHEMA_VERSION,
    WORKER_PYTHON_VERSION,
    WorkerBundleBuildResult,
    WorkerBundleManifest,
)

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_BUNDLE_RESOURCES_DIRECTORY = "_worker_bundle_resources"


def discover_worker_project_root() -> Path:
    """Gitやcurrent working directoryに依存せずbuild source rootを探す。"""

    root = _find_worker_project_root()
    if root is not None:
        return root
    raise RuntimeError("Unable to locate worker build root containing pyproject.toml and uv.lock")


def _find_worker_project_root() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if _is_shogiarena_project_root(candidate):
            return candidate
    return None


def _is_shogiarena_project_root(candidate: Path) -> bool:
    pyproject_path = candidate / "pyproject.toml"
    if not pyproject_path.is_file() or not (candidate / "uv.lock").is_file():
        return False
    try:
        return _read_project_name(pyproject_path) == "shogiarena"
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return False


def build_worker_bundle(
    *,
    output_path: Path,
    project_root: Path | None = None,
    target_os: Literal["linux"] = "linux",
    architecture: Literal["x86_64"] = "x86_64",
    python_version: str = WORKER_PYTHON_VERSION,
) -> WorkerBundleBuildResult:
    """wheel、locked dependencies、manifestから決定論的bundleを生成する。"""

    if not output_path.is_absolute():
        raise ValueError("worker bundle output path must be absolute")

    with tempfile.TemporaryDirectory(prefix="shogiarena-worker-bundle-") as temporary:
        build_dir = Path(temporary)
        discovered_root = project_root.resolve() if project_root is not None else _find_worker_project_root()
        root = discovered_root or _stage_installed_worker_project(build_dir / "installed-project")
        pyproject_path = root / "pyproject.toml"
        lock_path = root / "uv.lock"
        if not pyproject_path.is_file() or not lock_path.is_file():
            raise ValueError(f"worker build root must contain pyproject.toml and uv.lock: {root}")
        if _read_project_name(pyproject_path) != "shogiarena":
            raise ValueError(f"worker build root project.name must be 'shogiarena': {root}")
        wheel_dir = build_dir / "wheel"
        wheel_dir.mkdir()
        dependencies_path = build_dir / "requirements.lock"
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "315532800"
        _run_checked(
            ["uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(root)],
            environment=environment,
        )
        wheels = sorted(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"worker build must produce exactly one wheel, got {len(wheels)}")
        _run_checked(
            [
                "uv",
                "export",
                "--project",
                str(root),
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(dependencies_path),
            ],
            environment=environment,
        )
        uv_version_output = _run_checked(["uv", "--version"], environment=environment).strip()
        uv_version_parts = uv_version_output.split()
        if len(uv_version_parts) < 2 or uv_version_parts[0] != "uv":
            raise RuntimeError(f"unexpected uv --version output: {uv_version_output!r}")
        uv_version = " ".join(uv_version_parts[:2])
        wheel_path = wheels[0]
        package_version = _read_project_version(pyproject_path)
        wheel_digest = _sha256_file(wheel_path)
        lock_digest = _sha256_file(lock_path)
        dependencies_digest = _sha256_file(dependencies_path)
        deployment_id = sha256(
            _canonical_json_bytes(
                {
                    "protocol_version": GAME_EXECUTION_PROTOCOL_VERSION,
                    "package_version": package_version,
                    "wheel_sha256": wheel_digest,
                    "lock_sha256": lock_digest,
                    "dependencies_sha256": dependencies_digest,
                    "target_os": target_os,
                    "architecture": architecture,
                    "python_version": python_version,
                    "uv_version": uv_version,
                    "source_policy": "snapshot",
                }
            )
        ).hexdigest()
        manifest = WorkerBundleManifest(
            protocol_version=GAME_EXECUTION_PROTOCOL_VERSION,
            package_version=package_version,
            wheel_filename=wheel_path.name,
            wheel_sha256=wheel_digest,
            lock_sha256=lock_digest,
            dependencies_sha256=dependencies_digest,
            target_os=target_os,
            architecture=architecture,
            python_version=python_version,
            uv_version=uv_version,
            source_policy="snapshot",
            deployment_id=deployment_id,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_deterministic_bundle(
            output_path=output_path,
            wheel_path=wheel_path,
            dependencies_path=dependencies_path,
            manifest=manifest,
        )
    return WorkerBundleBuildResult(
        bundle_path=output_path,
        bundle_sha256=_sha256_file(output_path),
        manifest=manifest,
    )


def _stage_installed_worker_project(staging_root: Path) -> Path:
    package_root = Path(__file__).resolve().parents[4]
    resources_root = package_root / _BUNDLE_RESOURCES_DIRECTORY
    pyproject_source = resources_root / "pyproject.toml"
    lock_source = resources_root / "uv.lock"
    readme_source = resources_root / "README.md"
    if not pyproject_source.is_file() or not lock_source.is_file() or not readme_source.is_file():
        raise RuntimeError("Installed ShogiArena distribution does not contain worker bundle build resources")
    staging_root.mkdir(parents=True)
    shutil.copy2(pyproject_source, staging_root / "pyproject.toml")
    shutil.copy2(lock_source, staging_root / "uv.lock")
    shutil.copy2(readme_source, staging_root / "README.md")
    shutil.copytree(
        package_root,
        staging_root / "src" / "shogiarena",
        ignore=shutil.ignore_patterns(
            _BUNDLE_RESOURCES_DIRECTORY,
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ),
    )
    return staging_root


def read_worker_bundle_manifest(bundle_path: Path) -> WorkerBundleManifest:
    """bundle内manifestをstrict modelとして読み込む。"""

    with zipfile.ZipFile(bundle_path) as archive:
        raw = archive.read("manifest.json")
    return WorkerBundleManifest.model_validate_json(raw)


def _run_checked(command: list[str], *, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"worker bundle command failed ({command[0]}): {detail}")
    return completed.stdout


def _read_project_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError(f"project.version is missing from {pyproject_path}")
    return project["version"]


def _read_project_name(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise ValueError(f"project.name is missing from {pyproject_path}")
    return project["name"]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_deterministic_bundle(
    *,
    output_path: Path,
    wheel_path: Path,
    dependencies_path: Path,
    manifest: WorkerBundleManifest,
) -> None:
    entries = (
        ("manifest.json", manifest.canonical_bytes()),
        ("requirements.lock", dependencies_path.read_bytes()),
        (f"worker/{wheel_path.name}", wheel_path.read_bytes()),
    )
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload, compresslevel=9)


__all__ = [
    "WORKER_BUNDLE_SCHEMA_VERSION",
    "WORKER_PYTHON_VERSION",
    "WorkerBundleBuildResult",
    "WorkerBundleManifest",
    "build_worker_bundle",
    "discover_worker_project_root",
    "read_worker_bundle_manifest",
]
