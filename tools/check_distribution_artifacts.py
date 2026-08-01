"""Built wheel / sdist の dashboard assets、型情報、公開境界を検証する。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PACKAGE_ROOT = "shogiarena"
STATIC_ROOT = f"{PACKAGE_ROOT}/_core/interfaces/dashboard/static"
MANIFEST_PATH = f"{STATIC_ROOT}/dist/.vite/manifest.json"
BUILD_META_PATH = f"{STATIC_ROOT}/build-meta.json"
NOTICES_PATH = f"{STATIC_ROOT}/THIRD_PARTY_NOTICES.md"
WORKER_BUILD_PYPROJECT_PATH = f"{PACKAGE_ROOT}/_worker_bundle_resources/pyproject.toml"
WORKER_BUILD_LOCK_PATH = f"{PACKAGE_ROOT}/_worker_bundle_resources/uv.lock"
WORKER_BUILD_README_PATH = f"{PACKAGE_ROOT}/_worker_bundle_resources/README.md"
TSSHOGI_LICENSE_PATH = f"{STATIC_ROOT}/licenses/tsshogi-LICENSE"
ZOD_LICENSE_PATH = f"{STATIC_ROOT}/licenses/zod-LICENSE"
TSSHOGI_LICENSE_SHA256 = "c0ca95de4fb3a389348aa3b19a0a24c3fe154fca7d37a33081e47babb6a7d9e5"
ZOD_LICENSE_SHA256 = "3f1189b28e3866e0d979968d466b78f813f76827cfdca1fbb124cc0a5c8841f8"

# sdist は [tool.hatch.build.targets.sdist] の include 許可リストで決まる。
# ここは「許可リストが緩んだら気づく」ための否定検査として、代表的な dev-only を列挙する。
_SDIST_EXCLUDED_ROOTS = frozenset(
    {
        "_refs",
        ".sandbox",
        "tests",
        "stubs",
        "agent-docs",
        "visual-tests",
        ".github",
        ".devcontainer",
        ".vscode",
        ".serena",
        ".claude",
        "node_modules",
    }
)
# 全階層で拒否する秘密ファイルの命名。release-artifact-contract.md と対応させる。
_SECRET_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SECRET_FILE_NAMES = frozenset({".env", ".envrc", ".npmrc", ".pypirc"})
_SECRET_FILE_PREFIXES = (".env.", "id_rsa", "id_ecdsa", "id_ed25519")


def _is_secret_bearing_path(name: str) -> bool:
    parts = Path(name).parts
    if any(part == ".secrets" for part in parts):
        return True
    leaf = parts[-1] if parts else ""
    return leaf in _SECRET_FILE_NAMES or leaf.startswith(_SECRET_FILE_PREFIXES) or leaf.endswith(_SECRET_FILE_SUFFIXES)


def _find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("shogiarena-*.whl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not wheels:
        raise RuntimeError(f"No shogiarena wheel found under {dist_dir}")
    return wheels[0]


def _find_sdist(dist_dir: Path) -> Path:
    sdists = sorted(dist_dir.glob("shogiarena-*.tar.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not sdists:
        raise RuntimeError(f"No shogiarena sdist found under {dist_dir}")
    return sdists[0]


def _inspect_wheel(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
        required = {
            f"{PACKAGE_ROOT}/py.typed",
            MANIFEST_PATH,
            BUILD_META_PATH,
            NOTICES_PATH,
            WORKER_BUILD_PYPROJECT_PATH,
            WORKER_BUILD_LOCK_PATH,
            WORKER_BUILD_README_PATH,
            TSSHOGI_LICENSE_PATH,
            ZOD_LICENSE_PATH,
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError(f"Wheel is missing required files: {', '.join(missing)}")

        asset_prefix = f"{STATIC_ROOT}/dist/assets/"
        if not any(name.startswith(asset_prefix) and name.endswith(".js") for name in names):
            raise RuntimeError("Wheel contains no dashboard JavaScript bundle")
        if not any(name.startswith(asset_prefix) and name.endswith(".css") for name in names):
            raise RuntimeError("Wheel contains no dashboard CSS bundle")
        source_maps = sorted(name for name in names if name.startswith(asset_prefix) and name.endswith(".map"))
        if source_maps:
            raise RuntimeError(f"Wheel must not publish dashboard source maps: {', '.join(source_maps)}")

        # ランタイムが必要とするのは frontend/index.html だけ。TypeScript ソースや
        # テスト、ビルド設定が入っていたら、除外設定が退行している。
        frontend_prefix = f"{PACKAGE_ROOT}/_core/interfaces/dashboard/frontend/"
        unexpected_frontend = sorted(
            name for name in names if name.startswith(frontend_prefix) and name != f"{frontend_prefix}index.html"
        )
        if unexpected_frontend:
            preview = ", ".join(unexpected_frontend[:10])
            raise RuntimeError(f"Wheel must only ship the dashboard template, not frontend sources: {preview}")

        test_files = sorted(name for name in names if ".test." in name or "__tests__/" in name)
        if test_files:
            preview = ", ".join(test_files[:10])
            raise RuntimeError(f"Wheel must not ship tests: {preview}")

        manifest = archive.read(MANIFEST_PATH)
        build_meta = json.loads(archive.read(BUILD_META_PATH))
        expected_build_id = hashlib.sha256(manifest).hexdigest()[:12]
        if build_meta.get("build_id") != expected_build_id:
            raise RuntimeError(
                "Dashboard build metadata does not match the packaged Vite manifest: "
                f"expected={expected_build_id}, actual={build_meta.get('build_id')}"
            )

        notices = archive.read(NOTICES_PATH).decode("utf-8")
        tsshogi_license = archive.read(TSSHOGI_LICENSE_PATH)
        zod_license = archive.read(ZOD_LICENSE_PATH)
        if "tsshogi | 2.3.1 | MIT" not in notices or "zod | 3.25.76 | MIT" not in notices:
            raise RuntimeError("Frontend dependency provenance is incomplete in THIRD_PARTY_NOTICES.md")
        if hashlib.sha256(tsshogi_license).hexdigest() != TSSHOGI_LICENSE_SHA256:
            raise RuntimeError("Packaged tsshogi license text does not match the reviewed npm package")
        if hashlib.sha256(zod_license).hexdigest() != ZOD_LICENSE_SHA256:
            raise RuntimeError("Packaged zod license text does not match the reviewed npm package")


def _smoke_extracted_wheel(wheel_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="shogiarena-wheel-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        with zipfile.ZipFile(wheel_path) as archive:
            archive.extractall(temp_dir)

        sys.path.insert(0, str(temp_dir))
        try:
            import shogiarena
            from shogiarena._core.contexts.game_session.application.worker_bundle_builder import (
                build_worker_bundle,
            )
            from shogiarena._core.interfaces.dashboard.assets_writer import write_dashboard_assets
            from shogiarena._core.interfaces.dashboard.static_handler import StaticAssetsHandler

            module_path = Path(shogiarena.__file__).resolve()
            if not module_path.is_relative_to(temp_dir.resolve()):
                raise RuntimeError(f"Wheel smoke imported source tree instead of wheel contents: {module_path}")

            builtin_static = StaticAssetsHandler.dashboard_static_dir()
            if not (builtin_static / "dist" / ".vite" / "manifest.json").is_file():
                raise RuntimeError(f"dashboard_static_dir() does not resolve packaged assets: {builtin_static}")

            run_dashboard = temp_dir / "runtime" / "dashboard"
            write_dashboard_assets(
                run_dashboard,
                num_workers=1,
                should_overwrite_data=True,
                profiles=("tournament",),
            )
            if not (run_dashboard / "index.html").is_file():
                raise RuntimeError("Extracted wheel failed to materialize dashboard index.html")
            if not (run_dashboard / "static" / "dist" / ".vite" / "manifest.json").is_file():
                raise RuntimeError("Extracted wheel failed to materialize dashboard static assets")

            worker_bundle = build_worker_bundle(
                output_path=(temp_dir / "runtime" / "remote-worker-bundle.zip").resolve(),
            )
            if worker_bundle.manifest.package_version != shogiarena.__version__:
                raise RuntimeError(
                    "Extracted wheel produced a worker bundle with a different package version: "
                    f"wheel={shogiarena.__version__}, worker={worker_bundle.manifest.package_version}"
                )

            _check_installed_wheel_typing(temp_dir)
        finally:
            sys.path.pop(0)


def _check_installed_wheel_typing(extracted_wheel: Path) -> None:
    ty_executable = shutil.which("ty")
    if ty_executable is None:
        raise RuntimeError("Installed-wheel typing smoke requires the dev dependency 'ty'")

    consumer_path = extracted_wheel / "consumer.py"
    consumer_path.write_text(
        """from shogiarena.engine import InstancePool, JsonObject, UsiEngineSession, create_engine
from shogiarena.tournament import (
    DefaultRoot,
    ProgressReporterPort,
    RunStoragePort,
    TournamentRunResult,
    run_tournament,
)


async def consume(
    pool: InstancePool,
    storage: RunStoragePort,
    reporter: ProgressReporterPort,
    root: DefaultRoot,
) -> tuple[UsiEngineSession, TournamentRunResult]:
    options: JsonObject = {"USI_Hash": 256}
    engine = await create_engine("engine.yaml", extra_options=options, instance_pool=pool)
    result = await run_tournament(
        "tournament.yaml",
        run_dir="run",
        storage=storage,
        progress_reporter=reporter,
        root=root,
    )
    if result is None:
        raise RuntimeError("tournament was cancelled")
    return engine, result
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            ty_executable,
            "check",
            "--project",
            str(extracted_wheel),
            "--python",
            sys.executable,
            "--extra-search-path",
            str(extracted_wheel),
            "--error-on-warning",
            str(consumer_path),
        ],
        cwd=extracted_wheel,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        raise RuntimeError(f"Installed-wheel consumer typing smoke failed:\n{output}")


def _inspect_sdist(sdist_path: Path) -> None:
    with tarfile.open(sdist_path, mode="r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}

    roots = {name.split("/", maxsplit=1)[0] for name in names if "/" in name}
    if len(roots) != 1:
        raise RuntimeError(f"Sdist must contain exactly one archive root: {sorted(roots)}")
    root = roots.pop()
    required = {
        f"{root}/src/{PACKAGE_ROOT}/py.typed",
        f"{root}/src/{MANIFEST_PATH}",
        f"{root}/src/{BUILD_META_PATH}",
        f"{root}/src/{NOTICES_PATH}",
        f"{root}/src/{TSSHOGI_LICENSE_PATH}",
        f"{root}/src/{ZOD_LICENSE_PATH}",
        f"{root}/uv.lock",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"Sdist is missing required files: {', '.join(missing)}")

    static_asset_prefix = f"{root}/src/{STATIC_ROOT}/dist/assets/"
    source_maps = sorted(name for name in names if name.startswith(static_asset_prefix) and name.endswith(".map"))
    if source_maps:
        raise RuntimeError(f"Sdist must not publish dashboard source maps: {', '.join(source_maps)}")

    unsafe_paths = sorted(name for name in names if _is_secret_bearing_path(name))
    if unsafe_paths:
        raise RuntimeError(f"Sdist contains secret-bearing paths: {', '.join(unsafe_paths)}")

    # 除外設定の退行は「余計なものが入る」形で現れるので、必須ファイルの存在確認だけでは
    # 検出できない。dev-only ディレクトリが消えていないことを明示的に否定検査する。
    exported_dev_only = sorted(
        name for name in names if name.split("/", maxsplit=1)[-1].split("/", maxsplit=1)[0] in _SDIST_EXCLUDED_ROOTS
    )
    if exported_dev_only:
        preview = ", ".join(exported_dev_only[:10])
        raise RuntimeError(f"Sdist contains dev-only paths: {preview}")


def main() -> int:
    """最新のwheel / sdistを検査し、dashboardのproduction asset writerを実行する。"""

    dist_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    wheel_path = _find_wheel(dist_dir)
    sdist_path = _find_sdist(dist_dir)
    _inspect_wheel(wheel_path)
    _inspect_sdist(sdist_path)
    _smoke_extracted_wheel(wheel_path)
    print(f"Distribution artifact check passed: wheel={wheel_path}, sdist={sdist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
