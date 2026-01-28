"""Entry point for the ``shogiarena`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from shogiarena import __version__
from shogiarena.cli import commands
from shogiarena.cli.errors import CliError
from shogiarena.utils.common import settings as settings_mod
from shogiarena.utils.common.logging import setup_logging

CommandHandler = Callable[[argparse.Namespace], Any]
AsyncCommandHandler = Callable[[argparse.Namespace], Coroutine[Any, Any, Any]]

LOGGER = logging.getLogger("shogiarena.cli")

_DOTTED_OVERRIDE_FLAGS = {
    "--dashboard",
    "--rating",
    "--rules",
    "--spsa",
    "--sprt",
    "--system",
    "--tournament",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shogiarena",
        description="Unified command-line interface for Shogi Arena tooling.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Root logging level (default: INFO)",
    )
    parser.add_argument(
        "--debug-logger",
        action="append",
        dest="debug_loggers",
        metavar="LOGGER",
        help="Logger name to force DEBUG level (repeatable)",
    )
    parser.add_argument(
        "--root-dir",
        type=Path,
        help="Override the output directory for this invocation",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    commands.register(subparsers)
    return parser


def _configure_logging(level: str, debug_loggers: list[str] | None = None) -> None:
    setup_logging(level, debug_loggers=debug_loggers or [])
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


def _expand_dotted_overrides(argv: list[str]) -> list[str]:
    """Expand dotted override flags (e.g. --rules.time_control) into --rules tokens."""

    expanded: list[str] = []
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg.startswith("--") and "." in arg:
            base, suffix = arg.split(".", 1)
            if base in _DOTTED_OVERRIDE_FLAGS and suffix:
                if "=" in suffix:
                    key, value = suffix.split("=", 1)
                    if key:
                        expanded.append(base)
                        expanded.append(f"{key}={value}")
                        idx += 1
                        continue
                next_idx = idx + 1
                tokens: list[str] = []
                while next_idx < len(argv) and not argv[next_idx].startswith("--"):
                    tokens.append(argv[next_idx])
                    next_idx += 1
                if tokens:
                    expanded.append(base)
                    for token in tokens:
                        if token.startswith(f"{suffix}.") or token == suffix:
                            expanded.append(token)
                        else:
                            expanded.append(f"{suffix}.{token}")
                    idx = next_idx
                    continue
        expanded.append(arg)
        idx += 1
    return expanded


def main(argv: list[str] | None = None) -> None:
    raw_argv = argv if argv is not None else sys.argv[1:]
    expanded_argv = _expand_dotted_overrides(raw_argv)

    # Fast path for init/config commands: build minimal parser first
    # This avoids importing heavy modules (run, dashboard) when not needed
    command = expanded_argv[0] if expanded_argv and not expanded_argv[0].startswith("-") else None
    if command in {"init", "config"}:
        # Build minimal parser with only init/config commands for faster startup
        parser = argparse.ArgumentParser(
            prog="shogiarena", description="Unified command-line interface for Shogi Arena tooling."
        )
        parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
        parser.add_argument("--debug-logger", action="append", dest="debug_loggers", metavar="LOGGER")
        parser.add_argument("--root-dir", type=Path, help="Override the output directory for this invocation")
        subparsers = parser.add_subparsers(dest="command")
        subparsers.required = True

        from shogiarena.cli.commands.config import _config_init
        from shogiarena.cli.commands.config import register as config_register
        from shogiarena.utils.common.settings import (
            default_engine_dir_for_init,
            default_output_dir_for_init,
            default_settings_path,
        )

        config_register(subparsers)

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
        init_parser.add_argument("--non-interactive", "-y", action="store_true", help="Non-interactive mode")
        init_parser.add_argument(
            "--output-dir", type=Path, help=f"Output directory (default {default_output_dir_for_init()})"
        )
        init_parser.add_argument(
            "--engine-dir", type=Path, help=f"Engine cache directory (default {default_engine_dir_for_init()})"
        )
        init_parser.add_argument("--settings", type=Path, default=default_settings_path(), help="Path to settings.yaml")
        init_parser.add_argument("--force", action="store_true", help="Overwrite existing settings")
        init_parser.add_argument("--github-token", help="GitHub token for private repos")
        init_parser.set_defaults(handler=_config_init)

        args = parser.parse_args(expanded_argv)
    else:
        # Full parser for other commands
        parser = build_parser()
        args = parser.parse_args(expanded_argv)

    load_dotenv()
    command = getattr(args, "command", None)
    # Don't require settings.yaml - commands can work with default values
    # Artifact-based engines will fail with a clear error message if repos are not configured
    # Suppress warning for init/config commands since they create settings.yaml
    require_settings = False
    suppress_warning = command in {"init", "config"}
    try:
        settings_mod.configure_settings(
            root=args.root_dir, require_settings=require_settings, suppress_warning=suppress_warning
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    _configure_logging(args.log_level, args.debug_loggers)

    handler: CommandHandler | None = getattr(args, "handler", None)
    async_handler: AsyncCommandHandler | None = getattr(args, "async_handler", None)

    if handler is None and async_handler is None:
        parser.print_help()
        raise SystemExit(2)

    try:
        if async_handler is not None:
            asyncio.run(async_handler(args))
        elif handler is not None:
            handler(args)
    except CliError as exc:
        LOGGER.error(str(exc))
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        LOGGER.error("Cancelled by user (Ctrl+C)")
        raise SystemExit(130) from None


__all__ = ["main", "build_parser"]
