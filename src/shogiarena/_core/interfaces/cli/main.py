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
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.interfaces.cli.log_setup import setup_logging
from shogiarena._core.platform.settings import facade as settings_mod

CommandHandler = Callable[[argparse.Namespace], int | None]
AsyncCommandHandler = Callable[[argparse.Namespace], Coroutine[Any, Any, int | None]]

LOGGER = logging.getLogger("shogiarena.cli")

_DOTTED_OVERRIDE_FLAGS = {
    "--dashboard",
    "--logging",
    "--rating",
    "--rules",
    "--openbench",
    "--spsa",
    "--sprt",
    "--system",
    "--tournament",
}


class CliError(RuntimeError):
    """Base exception for CLI command failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CliArgumentError(CliError):
    """Raised when user-provided arguments are invalid."""


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
        "--output-dir",
        type=Path,
        help="Override the output directory for this invocation",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    from shogiarena._core.interfaces.cli.registry import register

    register(subparsers)
    return parser


def _configure_logging(level: str, debug_loggers: list[str] | None = None) -> None:
    setup_logging(level, debug_loggers=debug_loggers or [])
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


def _configure_runtime_settings(
    *,
    output_dir: Path | None,
    should_require_settings: bool,
    should_suppress_warning: bool,
) -> None:
    settings_mod.configure_settings(
        root=output_dir,
        should_require_settings=should_require_settings,
        should_suppress_warning=should_suppress_warning,
    )
    InstancePool.configure_default_local_instances_path(settings_mod.current_settings().output_dir)


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


def _reject_global_output_dir(argv: list[str]) -> None:
    """Reject global --output-dir for config fast path."""
    if not argv:
        return

    command_idx: int | None = None
    for idx, arg in enumerate(argv):
        if not arg.startswith("-"):
            command_idx = idx
            break
    if command_idx is None:
        return

    idx = 0
    while idx < command_idx:
        arg = argv[idx]
        if arg == "--output-dir":
            raise SystemExit(
                "--output-dir is not supported before the command for config init. "
                "Use `shogiarena config init --output-dir ...` instead."
            )
        if arg.startswith("--output-dir="):
            raise SystemExit(
                "--output-dir is not supported before the command for config init. "
                "Use `shogiarena config init --output-dir ...` instead."
            )
        idx += 1


def main(argv: list[str] | None = None) -> None:
    raw_argv = argv if argv is not None else sys.argv[1:]
    expanded_argv = _expand_dotted_overrides(raw_argv)

    # Fast path for config command: build minimal parser first.
    # This avoids importing heavy modules (run, dashboard) when not needed.
    command = next((arg for arg in expanded_argv if not arg.startswith("-")), None)
    if command == "config":
        _reject_global_output_dir(expanded_argv)
        # Build minimal parser with only config commands for faster startup.
        parser = argparse.ArgumentParser(
            prog="shogiarena", description="Unified command-line interface for Shogi Arena tooling."
        )
        parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
        parser.add_argument("--debug-logger", action="append", dest="debug_loggers", metavar="LOGGER")
        # NOTE: no global output-dir override here to avoid
        # clashing with `config init --output-dir` in the fast path.
        subparsers = parser.add_subparsers(dest="command")
        subparsers.required = True

        from shogiarena._core.interfaces.cli.config import register as config_register

        config_register(subparsers)

        args = parser.parse_args(expanded_argv)
    else:
        # Full parser for other commands
        parser = build_parser()
        args = parser.parse_args(expanded_argv)

    load_dotenv()
    command = args.command
    # Don't require settings.yaml - commands can work with default values
    # Artifact-based engines will fail with a clear error message if repos are not configured
    # Suppress warning for config command since `config init` may create settings.yaml
    should_require_settings = False
    should_suppress_warning = command == "config"
    try:
        output_dir = getattr(args, "output_dir", None)
        _configure_runtime_settings(
            output_dir=output_dir,
            should_require_settings=should_require_settings,
            should_suppress_warning=should_suppress_warning,
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
