"""Interactive wizard helpers for ``shogiarena config init``."""

from __future__ import annotations

import argparse
import getpass
import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from shogiarena._core.platform.settings.facade import load_settings
from shogiarena._core.platform.settings.loader import write_settings_file
from shogiarena._core.platform.settings.platform_paths import (
    default_engine_dir_for_init,
    default_output_dir_for_init,
    default_settings_path,
)
from shogiarena._core.shared.kernel.settings_loading.settings_models import (
    OpenBenchSettings,
    RepoSettings,
)

from .repo_setup import clone_repo
from .wizard_build_presets import (
    ensure_build_config,
    ensure_deeplearningshogi_overlay,
    ensure_fukauraou_overlay,
    ensure_yaneuraou_overlay,
)

logger = logging.getLogger(__name__)


def run_config_wizard(args: argparse.Namespace) -> None:
    """Run interactive configuration wizard."""
    settings = load_settings(should_require_settings=False, should_suppress_warning=True)
    settings_path = (args.settings or settings.settings_path or default_settings_path()).expanduser()

    # Use command-line arguments as defaults if provided.
    default_output = args.output_dir or default_output_dir_for_init()
    default_engine = args.engine_dir or default_engine_dir_for_init()

    output_dir = _prompt_path("Output directory", default_output)
    if output_dir is None:
        raise SystemExit("Output directory is required")

    engine_dir = _prompt_path("Engine cache directory", default_engine)
    if engine_dir is None:
        raise SystemExit("Engine cache directory is required")

    default_token = args.github_token if args.github_token else settings.github_token
    github_token = _prompt_github_token(default_token)
    repos = dict(settings.repos)
    overlays = dict(settings.overlays)
    openbench = _prompt_openbench_settings(settings.openbench)
    yaneuraou_repo_path = _maybe_add_yaneuraou_repo(repos, overlays, token=github_token or settings.github_token)
    if yaneuraou_repo_path is not None:
        _maybe_add_fukauraou_repo(
            repos,
            overlays,
            token=github_token or settings.github_token,
            base_repo_path=yaneuraou_repo_path,
        )
    _maybe_add_deeplearningshogi_repo(repos, overlays, token=github_token or settings.github_token)
    if _prompt_yes_no("他の repo を追加しますか？", is_default=False):
        while True:
            repo_input = _prompt_optional("Repo path or URL (blank to finish)")
            if not repo_input:
                break
            repo_url = repo_input if _looks_like_url(repo_input) else None
            repo_path = None
            if repo_url:
                default_name = _repo_name_from_url(repo_url)
                default_repo_path = default_output_dir_for_init().parent / "repos" / default_name
                repo_path = _prompt_path("Repo path", default_repo_path)
            else:
                repo_path = Path(repo_input).expanduser()
            if repo_path is None:
                raise SystemExit("Repo path is required")
            ensure_absolute_path(repo_path, "Repo path")
            default_name = repo_path.name or "repo"
            name = _prompt_optional(f"Repo name (blank to use {default_name})") or default_name
            build_default = default_output_dir_for_init().parent / "builds" / f"{name}.yaml"
            build_config = _prompt_path("Build config path", build_default)
            if build_config is None:
                raise SystemExit("Build config path is required")
            ensure_absolute_path(build_config, "Build config path")
            repos[name] = RepoSettings(
                name=name,
                path=repo_path,
                url=repo_url,
                build_config=build_config,
            )
            ensure_build_config(build_config, repo_name=name, confirm_overwrite_fn=_confirm_build_config_overwrite)
            if repo_url:
                _clone_repo_or_exit(repos[name], token=github_token or settings.github_token)

    output_dir.mkdir(parents=True, exist_ok=True)
    engine_dir.mkdir(parents=True, exist_ok=True)

    write_settings_file(
        settings_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
        repos=repos,
        github_token=github_token or settings.github_token,
        overlays=overlays,
        openbench=openbench,
    )

    print(f"Settings written to {settings_path}")


def ensure_absolute_path(path: Path | None, label: str) -> None:
    if path is None:
        return
    if not path.is_absolute():
        raise SystemExit(f"{label} must be an absolute path: {path}")


def _maybe_add_yaneuraou_repo(
    repos: dict[str, RepoSettings],
    overlays: dict[str, Path],
    *,
    token: str | None,
) -> Path | None:
    if not _prompt_yes_no("YaneuraOu を追加しますか？（推奨）", is_default=True):
        return None
    default_repo_base = default_output_dir_for_init().parent / "repos" / "YaneuraOu"
    repo_path = _prompt_path("YaneuraOu のクローン先パス", default_repo_base)
    if repo_path is None:
        raise SystemExit("YaneuraOu repo path is required")
    default_build_config = default_output_dir_for_init().parent / "builds" / "yaneuraou.yaml"
    build_config = _prompt_path("Build config path", default_build_config)
    if build_config is None:
        raise SystemExit("Build config path is required")
    ensure_absolute_path(repo_path, "Repo path")
    ensure_absolute_path(build_config, "Build config path")
    repos["YaneuraOu"] = RepoSettings(
        name="YaneuraOu",
        path=repo_path,
        url="https://github.com/yaneurao/YaneuraOu.git",
        build_config=build_config,
    )
    ensure_build_config(
        build_config,
        repo_name="YaneuraOu",
        confirm_overwrite_fn=_confirm_build_config_overwrite,
    )
    _clone_repo_or_exit(repos["YaneuraOu"], token=token)
    overlay_path = ensure_yaneuraou_overlay()
    overlays.setdefault("YaneuraOu", overlay_path)
    return repo_path


def _maybe_add_fukauraou_repo(
    repos: dict[str, RepoSettings],
    overlays: dict[str, Path],
    *,
    token: str | None,
    base_repo_path: Path | None,
) -> None:
    if base_repo_path is None:
        return
    if not _prompt_yes_no("FukauraOu (DLShogi互換) を追加しますか？", is_default=False):
        return
    repo_path = base_repo_path or (default_output_dir_for_init().parent / "repos" / "YaneuraOu")
    ensure_absolute_path(repo_path, "Repo path")
    default_build_config = default_output_dir_for_init().parent / "builds" / "fukauraou.yaml"
    build_config = _prompt_path("Build config path", default_build_config)
    if build_config is None:
        raise SystemExit("Build config path is required")
    ensure_absolute_path(build_config, "Build config path")
    cuda_path = _prompt_cuda_path(_detect_cuda_path())
    repos["FukauraOu"] = RepoSettings(
        name="FukauraOu",
        path=repo_path,
        url="https://github.com/yaneurao/YaneuraOu.git",
        build_config=build_config,
    )
    ensure_build_config(
        build_config,
        repo_name="FukauraOu",
        cuda_path=cuda_path,
        confirm_overwrite_fn=_confirm_build_config_overwrite,
    )
    _clone_repo_or_exit(repos["FukauraOu"], token=token)
    overlay_path = ensure_fukauraou_overlay()
    overlays.setdefault("FukauraOu", overlay_path)


def _maybe_add_deeplearningshogi_repo(
    repos: dict[str, RepoSettings],
    overlays: dict[str, Path],
    *,
    token: str | None,
) -> None:
    if not _prompt_yes_no("DeepLearningShogi を追加しますか？", is_default=False):
        return
    default_repo_base = default_output_dir_for_init().parent / "repos" / "DeepLearningShogi"
    repo_path = _prompt_path("DeepLearningShogi のクローン先パス", default_repo_base)
    if repo_path is None:
        raise SystemExit("DeepLearningShogi repo path is required")
    default_build_config = default_output_dir_for_init().parent / "builds" / "deeplearningshogi.yaml"
    build_config = _prompt_path("Build config path", default_build_config)
    if build_config is None:
        raise SystemExit("Build config path is required")
    ensure_absolute_path(repo_path, "Repo path")
    ensure_absolute_path(build_config, "Build config path")
    repos["DeepLearningShogi"] = RepoSettings(
        name="DeepLearningShogi",
        path=repo_path,
        url="https://github.com/TadaoYamaoka/DeepLearningShogi.git",
        build_config=build_config,
    )
    ensure_build_config(
        build_config,
        repo_name="DeepLearningShogi",
        confirm_overwrite_fn=_confirm_build_config_overwrite,
    )
    _clone_repo_or_exit(repos["DeepLearningShogi"], token=token)
    overlay_path = ensure_deeplearningshogi_overlay()
    overlays.setdefault("DeepLearningShogi", overlay_path)


def _detect_cuda_path() -> Path | None:
    nvcc_path = shutil.which("nvcc")
    if not nvcc_path:
        return None
    try:
        subprocess.run([nvcc_path, "-V"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.debug("Failed to validate nvcc at %s: %s", nvcc_path, exc)
        return None
    cuda_root = Path(nvcc_path).resolve().parent.parent
    if (cuda_root / "bin" / "nvcc").exists():
        return cuda_root
    return None


def _prompt_cuda_path(detected: Path | None) -> Path | None:
    if detected is not None:
        if not _prompt_yes_no(f"CUDA を検出しました ({detected}). build config に設定しますか？", is_default=True):
            return None
        path = _prompt_path("CUDA root path", detected)
        if path is None:
            raise SystemExit("CUDA path is required")
        ensure_absolute_path(path, "CUDA path")
        return path
    if not _prompt_yes_no("CUDA path を build config に設定しますか？", is_default=False):
        return None
    path = _prompt_path("CUDA root path", Path("/usr/local/cuda"))
    if path is None:
        raise SystemExit("CUDA path is required")
    ensure_absolute_path(path, "CUDA path")
    return path


def _prompt_optional(label: str) -> str:
    return input(f"{label}: ").strip()


def _prompt_secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _prompt_github_token(existing: str | None) -> str | None:
    if not _prompt_yes_no("private repo を使うなら GitHub token を設定しますか？", is_default=False):
        return None
    print("トークン作成ページ: https://github.com/settings/personal-access-tokens")
    print("必要権限: 対象リポジトリの Contents (Read-only で OK)")
    token = _prompt_secret("GitHub token")
    return token or existing


def _prompt_openbench_settings(existing: OpenBenchSettings | None) -> OpenBenchSettings | None:
    if not _prompt_yes_no("OpenBench/ShogiBench 連携設定を行いますか？", is_default=False):
        return existing

    default_server = existing.server if existing is not None else ""
    default_username = existing.username if existing is not None else ""
    default_env = existing.password_env if existing is not None else "OPENBENCH_PASSWORD"

    server = _prompt_optional(f"OpenBench server URL (blank to keep: {default_server or 'none'})")
    username = _prompt_optional(f"OpenBench username (blank to keep: {default_username or 'none'})")
    password_env = _prompt_optional(f"Password env var (blank to keep: {default_env})")

    resolved_server = server or default_server
    resolved_username = username or default_username
    resolved_env = password_env or default_env

    if not resolved_server or not resolved_username:
        print("OpenBench 設定は server/username が必要なため保存しません。")
        return existing
    return OpenBenchSettings(
        server=resolved_server,
        username=resolved_username,
        password_env=resolved_env,
    )


def _clone_repo_or_exit(repo: RepoSettings, *, token: str | None) -> None:
    if not repo.url:
        raise SystemExit("clone requires repo.url")
    try:
        clone_repo(repo.url, repo.path, token=token)
    except SystemExit as exc:
        print("git clone に失敗しました。private repo の場合は GitHub の認証が必要です。")
        print("settings.yaml の github_token を設定してください。")
        raise exc


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "ssh", "git"} and bool(parsed.netloc or parsed.path)


def _repo_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    tail = Path(parsed.path).name if parsed.path else ""
    if not tail and parsed.scheme == "ssh":
        tail = Path(parsed.path).name
    name = tail.replace(".git", "") if tail else "repo"
    return name or "repo"


def _confirm_build_config_overwrite(question: str) -> bool:
    return _prompt_yes_no(question, is_default=False)


def _prompt_path(label: str, current: Path | None, *, should_allow_clear: bool = False) -> Path | None:
    default = str(current) if current else ""
    suffix = " (use '-' to clear)" if should_allow_clear else ""
    prompt = f"{label} [{default}]{suffix}: "
    entered = input(prompt).strip()
    if not entered:
        if current is None:
            raise SystemExit(f"{label} is required")
        return current
    if should_allow_clear and entered == "-":
        return None
    return Path(entered).expanduser()


def _prompt_yes_no(question: str, *, is_default: bool = False) -> bool:
    hint = "Y/n" if is_default else "y/N"
    answer = input(f"{question} [{hint}]: ").strip().lower()
    if not answer:
        return is_default
    return answer in {"y", "yes"}


__all__ = ["ensure_absolute_path", "run_config_wizard"]
