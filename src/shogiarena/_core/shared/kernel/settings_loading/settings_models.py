from __future__ import annotations

import os
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


DEFAULT_GITHUB_TOKEN_ENV = "SHOGIARENA_GITHUB_TOKEN"


@dataclass(frozen=True)
class ArenaSettings:
    """Resolved runtime settings."""

    output_dir: Path
    engine_dir: Path
    settings_path: Path
    repos: dict[str, RepoSettings]
    overlays: dict[str, Path]
    openbench: OpenBenchSettings | None
    github_token_env: str = DEFAULT_GITHUB_TOKEN_ENV

    @property
    def github_token(self) -> str | None:
        """GitHub token を環境変数から解決する。

        token は settings ファイルには保存せず、``github_token_env`` が指す
        環境変数から読む（``OpenBenchSettings.password_env`` と同じ方式）。
        これにより settings ファイルが秘密を持たなくなり、パーミッションを
        強制できない環境（Windows）でも漏洩経路にならない。

        Returns:
            解決したトークン。未設定なら ``None``。
        """
        return os.environ.get(self.github_token_env, "").strip() or None


__all__ = [
    "DEFAULT_GITHUB_TOKEN_ENV",
    "ArenaSettings",
    "OpenBenchSettings",
    "RepoSettings",
]
