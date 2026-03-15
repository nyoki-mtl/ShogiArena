"""Shared WDL (win/draw/loss) counter typing."""

from __future__ import annotations

from typing import TypedDict


class WdlCounts(TypedDict):
    wins: int
    losses: int
    draws: int


__all__ = ["WdlCounts"]
