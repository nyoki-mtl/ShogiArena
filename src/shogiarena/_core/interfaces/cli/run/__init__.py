"""Forwarding exports for the ``shogiarena run`` command group."""

from __future__ import annotations

import argparse
from importlib import import_module
from types import ModuleType

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    module = import_module("shogiarena._core.interfaces.cli.run.command")
    if not isinstance(module, ModuleType):
        raise TypeError(f"expected module, got {type(module).__name__}")
    module.register(subparsers)
