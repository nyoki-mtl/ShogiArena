"""Subcommand registration for the shogiarena CLI."""

from __future__ import annotations

import argparse

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register all CLI subcommands."""

    # Register lightweight config command first for faster startup
    from . import config

    config.register(subparsers)

    # Register heavier commands (lazy import to speed up config command startup)
    from . import dashboard, internal, replay_position, results, run

    run.register(subparsers)
    replay_position.register(subparsers)
    results.register(subparsers)
    dashboard.register(subparsers)
    internal.register(subparsers)
