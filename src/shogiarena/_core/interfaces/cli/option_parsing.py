"""Option parsing helpers for CLI command implementations."""

from __future__ import annotations

from shogiarena._core.interfaces.cli.main import CliArgumentError


def parse_option_overrides(option_list: list[str] | None) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` option overrides."""

    if not option_list:
        return {}
    overrides: dict[str, str] = {}
    for raw in option_list:
        if "=" not in raw:
            raise CliArgumentError(f"invalid option override (expected KEY=VALUE): {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CliArgumentError("option key must not be empty")
        overrides[key] = value.strip()
    return overrides


__all__ = ["parse_option_overrides"]
