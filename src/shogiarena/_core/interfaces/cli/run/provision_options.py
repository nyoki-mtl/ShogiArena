"""Remote artifact provisioning CLI contract."""

from __future__ import annotations

import argparse
from typing import Literal

ProvisionMode = Literal["cas", "preplaced"]


def parse_provision_mode(value: str) -> ProvisionMode:
    """Legacy none/forceをrejectしcurrent provisioning modeをparseする。"""

    normalized = value.strip().lower()
    if normalized in {"cas", "preplaced"}:
        return normalized
    if normalized == "none":
        raise argparse.ArgumentTypeError(
            "--provision none was removed; use --provision preplaced and provide each resource's "
            "absolute remote path plus expected SHA-256 in SHOGIARENA_REMOTE_PREPLACED_RESOURCES"
        )
    if normalized == "force":
        raise argparse.ArgumentTypeError("--provision force was removed; immutable endpoint-aware CAS is now mandatory")
    raise argparse.ArgumentTypeError("--provision must be one of: cas, preplaced")


__all__ = ["ProvisionMode", "parse_provision_mode"]
