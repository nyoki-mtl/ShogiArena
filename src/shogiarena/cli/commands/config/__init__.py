"""Implementation of the ``shogiarena config`` command."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

from shogiarena.utils.common.settings import (
    RepoSettings,
    default_engine_dir_for_init,
    default_output_dir_for_init,
    default_settings_path,
    load_settings,
    write_settings_file,
)

from ._setup_utils import clone_repo


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "config",
        help="Configure Shogi Arena settings",
    )
    config_sub = parser.add_subparsers(dest="config_command")
    config_sub.required = True

    init_parser = config_sub.add_parser(
        "init",
        help="Initialize settings.yaml and runtime directories (canonical command)",
        description=(
            "Initialize Shogi Arena settings and runtime directories. "
            "By default, runs interactively to guide you through setup. "
            "Use --non-interactive for CI/automation. "
            "'shogiarena init' is a compatibility alias for this command."
        ),
    )
    init_parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Non-interactive mode: use command-line arguments instead of prompts",
    )
    init_parser.add_argument(
        "--output-dir",
        type=Path,
        help=f"Output directory (default {default_output_dir_for_init()})",
    )
    init_parser.add_argument(
        "--engine-dir",
        type=Path,
        help=f"Engine cache directory (default {default_engine_dir_for_init()})",
    )
    init_parser.add_argument(
        "--settings",
        type=Path,
        default=default_settings_path(),
        help="Path to settings.yaml (default resolves to platform config directory)",
    )
    init_parser.add_argument(
        "--github-token",
        help="GitHub token for private repos (stored in settings.yaml)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing settings",
    )
    init_parser.set_defaults(handler=_config_init)

    show_parser = config_sub.add_parser("show", help="Display the current settings")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    show_parser.set_defaults(handler=_config_show)

    repo_parser = config_sub.add_parser("repo", help="Manage artifact repositories")
    repo_sub = repo_parser.add_subparsers(dest="repo_command")
    repo_sub.required = True

    repo_set = repo_sub.add_parser("set", help="Add or update a repo entry")
    repo_set.add_argument("name")
    repo_set.add_argument("--path", required=True, type=Path)
    repo_set.add_argument("--url", help="Optional git remote URL")
    repo_set.add_argument(
        "--build-config",
        required=True,
        type=Path,
        help="Absolute path to build config YAML",
    )
    repo_set.add_argument(
        "--clone",
        action="store_true",
        help="Clone the repo into the specified path",
    )
    repo_set.set_defaults(handler=_config_repo_set)

    repo_rm = repo_sub.add_parser("remove", help="Remove a repo entry")
    repo_rm.add_argument("name")
    repo_rm.set_defaults(handler=_config_repo_remove)


def config_init(args: argparse.Namespace) -> None:
    """Initialize settings.yaml and runtime directories.

    This is the canonical implementation for initialization.
    Can be called from both 'config init' and 'init' commands.
    """
    settings_path = args.settings.expanduser()
    if settings_path.exists() and not args.force:
        raise SystemExit(f"settings.yaml already exists at {settings_path}. Use --force to overwrite.")

    output_dir = (args.output_dir or default_output_dir_for_init()).expanduser()
    engine_dir = (args.engine_dir or default_engine_dir_for_init()).expanduser()

    output_dir.mkdir(parents=True, exist_ok=True)
    engine_dir.mkdir(parents=True, exist_ok=True)

    write_settings_file(
        settings_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
        repos={},
        github_token=str(getattr(args, "github_token", "")).strip() or None,
        overlays={},
    )
    print(f"Settings written to {settings_path}")


def _config_init(args: argparse.Namespace) -> None:
    """Handler for 'config init' command.

    By default runs interactively (wizard mode) if TTY is available.
    Automatically falls back to non-interactive mode if TTY is not available.
    Use --non-interactive to force non-interactive mode.
    """
    if args.non_interactive or not sys.stdin.isatty():
        # Non-interactive mode: use command-line arguments
        # Also used automatically when TTY is not available (e.g., CI environments)
        config_init(args)
    else:
        # Interactive mode: run wizard
        _config_wizard(args)


def _config_show(args: argparse.Namespace) -> None:
    settings = load_settings(require_settings=False)
    if args.json:
        import json

        payload = {
            "settings_path": str(settings.settings_path),
            "output_dir": str(settings.output_dir),
            "engine_dir": str(settings.engine_dir),
            "repos": {
                name: {
                    "path": str(spec.path),
                    "url": spec.url,
                    "build_config": str(spec.build_config) if spec.build_config else None,
                }
                for name, spec in settings.repos.items()
            },
            "github_token": "(set)" if settings.github_token else None,
            "overlays": {name: str(path) for name, path in settings.overlays.items()},
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print(f"settings_path : {settings.settings_path}")
    print(f"output_dir    : {settings.output_dir}")
    print(f"engine_dir    : {settings.engine_dir}")
    if settings.github_token:
        print("github_token  : (set)")
    if settings.repos:
        print("repos:")
        for name, spec in settings.repos.items():
            url = f" ({spec.url})" if spec.url else ""
            build = f" [build={spec.build_config}]" if spec.build_config else ""
            print(f"  - {name}: {spec.path}{url}{build}")
    else:
        print("repos: (none)")
    if settings.overlays:
        print("overlays:")
        for name, path in settings.overlays.items():
            print(f"  - {name}: {path}")
    else:
        print("overlays: (none)")


def _config_repo_set(args: argparse.Namespace) -> None:
    settings = load_settings(require_settings=False)
    repos = dict(settings.repos)
    _ensure_absolute_path(args.path, "--path")
    _ensure_absolute_path(args.build_config, "--build-config")
    spec = RepoSettings(
        name=str(args.name),
        path=args.path.expanduser(),
        url=str(args.url) if args.url else None,
        build_config=args.build_config.expanduser(),
    )
    repos[spec.name] = spec
    write_settings_file(
        settings.settings_path,
        output_dir=settings.output_dir,
        engine_dir=settings.engine_dir,
        repos=repos,
        github_token=settings.github_token,
        overlays=settings.overlays,
    )

    if args.clone:
        if not spec.url:
            raise SystemExit("--clone requires --url")
        clone_repo(spec.url, spec.path)

    print(f"Updated repo '{spec.name}'")


def _config_repo_remove(args: argparse.Namespace) -> None:
    settings = load_settings(require_settings=False)
    repos = dict(settings.repos)
    if args.name not in repos:
        raise SystemExit(f"repo not found: {args.name}")
    del repos[args.name]
    write_settings_file(
        settings.settings_path,
        output_dir=settings.output_dir,
        engine_dir=settings.engine_dir,
        repos=repos,
        github_token=settings.github_token,
        overlays=settings.overlays,
    )
    print(f"Removed repo '{args.name}'")


def _config_wizard(args: argparse.Namespace) -> None:
    """Interactive wizard for configuring settings.

    Called from 'config init' when running in interactive mode.
    Uses command-line arguments as default values if provided.
    """
    settings = load_settings(require_settings=False, suppress_warning=True)
    settings_path = (args.settings or settings.settings_path or default_settings_path()).expanduser()

    # Use command-line arguments as defaults if provided
    # For init command, always use init defaults (platform-standard directories)
    # unless explicitly overridden via command-line arguments
    default_output = args.output_dir or default_output_dir_for_init()
    default_engine = args.engine_dir or default_engine_dir_for_init()

    output_dir = _prompt_path("Output directory", default_output)
    if output_dir is None:
        raise SystemExit("Output directory is required")

    engine_dir = _prompt_path("Engine cache directory", default_engine)
    if engine_dir is None:
        raise SystemExit("Engine cache directory is required")

    # Use command-line argument as default if provided
    default_token = args.github_token if hasattr(args, "github_token") and args.github_token else settings.github_token
    github_token = _prompt_github_token(default_token)
    repos = dict(settings.repos)
    overlays = dict(settings.overlays)
    _maybe_add_yaneuraou_repo(repos, overlays, token=github_token or settings.github_token)
    if _prompt_yes_no("他の repo を追加しますか？", default=False):
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
            _ensure_absolute_path(repo_path, "Repo path")
            default_name = repo_path.name or "repo"
            name = _prompt_optional(f"Repo name (blank to use {default_name})") or default_name
            build_default = default_output_dir_for_init().parent / "builds" / f"{name}.yaml"
            build_config = _prompt_path("Build config path", build_default)
            if build_config is None:
                raise SystemExit("Build config path is required")
            _ensure_absolute_path(build_config, "Build config path")
            repos[name] = RepoSettings(
                name=name,
                path=repo_path,
                url=repo_url,
                build_config=build_config,
            )
            _ensure_build_config(build_config, repo_name=name)
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
    )

    print(f"Settings written to {settings_path}")


def _ensure_absolute_path(path: Path | None, label: str) -> None:
    if path is None:
        return
    if not path.is_absolute():
        raise SystemExit(f"{label} must be an absolute path: {path}")


def _maybe_add_yaneuraou_repo(
    repos: dict[str, RepoSettings],
    overlays: dict[str, Path],
    *,
    token: str | None,
) -> None:
    if not _prompt_yes_no("YaneuraOu を追加しますか？（推奨）", default=True):
        return
    default_repo_base = default_output_dir_for_init().parent / "repos" / "YaneuraOu"
    repo_path = _prompt_path("YaneuraOu のクローン先パス", default_repo_base)
    if repo_path is None:
        raise SystemExit("YaneuraOu repo path is required")
    default_build_config = default_output_dir_for_init().parent / "builds" / "yaneuraou.yaml"
    build_config = _prompt_path("Build config path", default_build_config)
    if build_config is None:
        raise SystemExit("Build config path is required")
    _ensure_absolute_path(repo_path, "Repo path")
    _ensure_absolute_path(build_config, "Build config path")
    repos["YaneuraOu"] = RepoSettings(
        name="YaneuraOu",
        path=repo_path,
        url="https://github.com/yaneurao/YaneuraOu.git",
        build_config=build_config,
    )
    _ensure_build_config(build_config, repo_name="YaneuraOu")
    _clone_repo_or_exit(repos["YaneuraOu"], token=token)
    overlay_path = _ensure_yaneuraou_overlay()
    overlays.setdefault("YaneuraOu", overlay_path)


def _write_default_yaneuraou_build_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_windows = sys.platform.startswith("win")
    artifacts = (
        [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc.exe",
                "chmod": "755",
            }
        ]
        if is_windows
        else [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc",
                "chmod": "755",
            }
        ]
    )
    payload = {
        "work_dir": "{repo.path}/source",
        "defaults": {
            "compiler": "clang++",
            "target": "tournament",
            "jobs": 4,
        },
        "commands": [
            ["make", "clean"],
            [
                "make",
                "-j{opts.jobs}",
                "{opts.target}",
                "TARGET_CPU={opts.target_cpu}",
                "COMPILER={opts.compiler}",
                "YANEURAOU_EDITION={opts.edition}",
            ],
        ],
        "artifacts": artifacts,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _ensure_yaneuraou_overlay() -> Path:
    overlay_path = default_output_dir_for_init().parent / "overlays" / "YaneuraOu.yaml"
    if overlay_path.exists():
        return overlay_path
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": {
            "BookFile": "no_book",
            "NetworkDelay": 0,
            "NetworkDelay2": 0,
        }
    }
    overlay_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return overlay_path


def _write_default_build_config(path: Path, *, repo_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "work_dir": "{repo.path}",
        "source_dir": "{repo.path}",
        "env": {
            "TARGET_CPU": "{opts.target_cpu}",
            "EDITION": "{opts.edition}",
        },
        "commands": [
            ["make", "clean"],
            ["make", "all"],
        ],
        "artifacts": [
            {
                "path": "{source_dir}/" + repo_name,
                "chmod": "755",
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _ensure_build_config(path: Path, *, repo_name: str) -> None:
    if not path.exists():
        if repo_name == "YaneuraOu":
            _write_default_yaneuraou_build_config(path)
        else:
            _write_default_build_config(path, repo_name=repo_name)
        print(f"Wrote default build_config to {path}")
        return
    ok, msg = _validate_build_config(path)
    if ok:
        return
    print(f"build_config is invalid: {msg}")
    if not _prompt_yes_no("Overwrite with default build_config?", default=False):
        raise SystemExit("Invalid build_config; aborting.")
    if repo_name == "YaneuraOu":
        _write_default_yaneuraou_build_config(path)
    else:
        _write_default_build_config(path, repo_name=repo_name)
    print(f"Rewrote build_config at {path}")


def _prompt_optional(label: str) -> str:
    return input(f"{label}: ").strip()


def _prompt_secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _prompt_github_token(existing: str | None) -> str | None:
    if not _prompt_yes_no("private repo を使うなら GitHub token を設定しますか？", default=False):
        return None
    print("トークン作成ページ: https://github.com/settings/personal-access-tokens")
    print("必要権限: 対象リポジトリの Contents (Read-only で OK)")
    token = _prompt_secret("GitHub token")
    return token or existing


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


def _validate_build_config(path: Path) -> tuple[bool, str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        return False, f"failed to read: {exc}"
    if not isinstance(data, dict):
        return False, "top-level is not a mapping"
    commands = data.get("commands")
    if not isinstance(commands, list):
        return False, "commands must be a list"
    for cmd in commands:
        if not isinstance(cmd, list) or not all(isinstance(arg, str | int | float) for arg in cmd):
            return False, "commands entries must be lists of scalars"
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False, "artifacts must be a non-empty list"
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item.get("path"):
            return False, "artifacts entries must include a non-empty path"
    return True, ""


def _prompt_path(label: str, current: Path | None, *, allow_clear: bool = False) -> Path | None:
    default = str(current) if current else ""
    suffix = " (use '-' to clear)" if allow_clear else ""
    prompt = f"{label} [{default}]{suffix}: "
    entered = input(prompt).strip()
    if not entered:
        if current is None:
            raise SystemExit(f"{label} is required")
        return current
    if allow_clear and entered == "-":
        return None
    return Path(entered).expanduser()


def _prompt_yes_no(question: str, *, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{question} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


__all__ = ["register", "config_init"]
