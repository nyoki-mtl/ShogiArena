from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoSettings:
    """Repository configuration for artifact builds."""

    name: str
    path: Path
    url: str | None = None
    build_config: Path | None = None


@dataclass(frozen=True)
class OpenBenchSettings:
    server: str | None = None
    username: str | None = None
    password_env: str = "OPENBENCH_PASSWORD"


@dataclass(frozen=True)
class ArenaSettings:
    """Resolved runtime settings."""

    output_dir: Path
    engine_dir: Path
    settings_path: Path
    repos: dict[str, RepoSettings]
    github_token: str | None
    overlays: dict[str, Path]
    openbench: OpenBenchSettings | None


__all__ = [
    "ArenaSettings",
    "OpenBenchSettings",
    "RepoSettings",
]
