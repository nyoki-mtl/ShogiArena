"""Subcommand registration for the shogiarena CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from shogiarena.cli.commands.config import _config_init
from shogiarena.utils.common.settings import (
    default_engine_dir_for_init,
    default_output_dir_for_init,
    default_settings_path,
)

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register all CLI subcommands."""

    # Register lightweight commands first (init/config) for faster startup
    from . import config

    config.register(subparsers)

    # Register 'init' as an alias for 'config init'
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize Shogi Arena settings and output directories (alias for 'config init')",
        description=(
            "This is a compatibility alias for 'shogiarena config init'. "
            "By default runs interactively. Use --non-interactive for CI/automation. "
            "The canonical command is 'config init'."
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
        "--force",
        action="store_true",
        help="Overwrite existing settings",
    )
    init_parser.add_argument(
        "--github-token",
        help="GitHub token for private repos (stored in settings.yaml)",
    )
    init_parser.set_defaults(handler=_config_init)

    # Register heavier commands (lazy import to speed up init/config commands)
    from . import dashboard, internal, run

    run.register(subparsers)
    dashboard.register(subparsers)
    internal.register(subparsers)
