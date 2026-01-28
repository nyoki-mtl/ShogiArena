"""Internal-only CLI subcommands for ShogiArena."""

from __future__ import annotations

import argparse

from . import remote

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "_internal",
        help=argparse.SUPPRESS,
    )
    internal_sub = parser.add_subparsers(dest="internal_command")
    internal_sub.required = True
    remote.register(internal_sub)
