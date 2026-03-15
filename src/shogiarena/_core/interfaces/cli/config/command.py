"""Implementation of the ``shogiarena config`` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shogiarena._core.platform.settings.facade import load_settings
from shogiarena._core.platform.settings.loader import write_settings_file
from shogiarena._core.platform.settings.platform_paths import (
    default_engine_dir_for_init,
    default_output_dir_for_init,
    default_settings_path,
)
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings

from .repo_setup import clone_repo
from .wizard import ensure_absolute_path, run_config_wizard


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
            "Use --non-interactive for CI/automation."
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

    This is the canonical implementation for `config init`.
    """
    settings_path = args.settings.expanduser()
    if settings_path.exists() and not args.force:
        raise SystemExit(f"settings.yaml already exists at {settings_path}. Use --force to overwrite.")

    output_dir = (args.output_dir or default_output_dir_for_init()).expanduser()
    engine_dir = (args.engine_dir or default_engine_dir_for_init()).expanduser()

    output_dir.mkdir(parents=True, exist_ok=True)
    engine_dir.mkdir(parents=True, exist_ok=True)

    github_token = str(args.github_token or "").strip()
    write_settings_file(
        settings_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
        repos={},
        github_token=github_token if github_token else None,
        overlays={},
        openbench=None,
    )
    print(f"Settings written to {settings_path}")


def _config_init(args: argparse.Namespace) -> None:
    """Handler for 'config init' command.

    By default runs interactively (wizard mode) if TTY is available.
    Automatically falls back to non-interactive mode if TTY is not available.
    Use --non-interactive to force non-interactive mode.
    """
    if args.non_interactive or not sys.stdin.isatty():
        config_init(args)
    else:
        run_config_wizard(args)


def _config_show(args: argparse.Namespace) -> None:
    settings = load_settings(should_require_settings=False)
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
            "openbench": (
                {
                    "server": settings.openbench.server,
                    "username": settings.openbench.username,
                    "password_env": settings.openbench.password_env,
                }
                if settings.openbench is not None
                else None
            ),
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
    if settings.openbench is not None:
        print("openbench:")
        print(f"  server       : {settings.openbench.server}")
        print(f"  username     : {settings.openbench.username}")
        print(f"  password_env : {settings.openbench.password_env}")
    else:
        print("openbench: (none)")


def _config_repo_set(args: argparse.Namespace) -> None:
    settings = load_settings(should_require_settings=False)
    repos = dict(settings.repos)
    ensure_absolute_path(args.path, "--path")
    ensure_absolute_path(args.build_config, "--build-config")
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
        openbench=settings.openbench,
    )

    if args.clone:
        if not spec.url:
            raise SystemExit("--clone requires --url")
        clone_repo(spec.url, spec.path)

    print(f"Updated repo '{spec.name}'")


def _config_repo_remove(args: argparse.Namespace) -> None:
    settings = load_settings(should_require_settings=False)
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
        openbench=settings.openbench,
    )
    print(f"Removed repo '{args.name}'")


__all__ = ["register"]
