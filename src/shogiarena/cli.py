"""Public CLI entrypoints."""

from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError, build_parser, main

__all__ = ["CliArgumentError", "CliError", "build_parser", "main"]
