"""Configuration models for instances."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal


class InstanceType(StrEnum):
    """Types of instances supported."""

    LOCAL = "local"
    SSH = "ssh"


@dataclass
class InstanceConfig:
    """Configuration for an instance from YAML.

    For SSH instances, `project_root` defines the remote project root directory
    (defaults to `$HOME/ShogiArena-remote`). `engine_dir` is derived as
    `{project_root}/data/engines` and should not be specified directly in YAML.
    """

    name: str
    type: InstanceType
    # Derived for SSH as {project_root}/data/engines; must be empty for LOCAL
    engine_dir: str
    # SSH-only optional override of project root; if empty, defaults to ~/ShogiArena-remote
    project_root: str = ""
    host: str | None = None
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    # slots is None means "auto" (resolved from metrics when available)
    slots: int | None = None
    max_engines: int | None = None
    tags: list[str] = field(default_factory=list)
    operating_system: Literal["linux"] = "linux"
    architecture: Literal["x86_64"] = "x86_64"
    is_strict_host_key_checking: bool = True
    should_install_requirements: bool = False

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.name or not self.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError("Instance name must be alphanumeric with hyphens, underscores, or dots")

        if self.max_engines is not None and not isinstance(self.max_engines, int):
            raise TypeError("max_engines must be an integer when provided")

        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if isinstance(self.slots, bool):
            raise TypeError("slots must be an integer or null")
        if self.slots is not None and not isinstance(self.slots, int):
            raise TypeError("slots must be an integer or null")
        elif self.slots is not None and self.slots <= 0:
            raise ValueError("slots must be positive or null (null means auto)")

        # Derive engine_dir for SSH from project_root when present
        if self.type == InstanceType.LOCAL:
            if self.engine_dir:
                raise ValueError("engine_dir must not be set for local instances")
            # Ignore project_root for local
            self.project_root = ""
        elif self.type == InstanceType.SSH:
            # Normalize project_root default
            if not self.project_root:
                # Default to remote home (do not expand locally)
                self.project_root = "$HOME/ShogiArena-remote"
            # Derive engine_dir from project_root consistently
            if "\\" in self.project_root:
                raise ValueError("SSH project_root must use POSIX separators")
            self.engine_dir = str(PurePosixPath(self.project_root) / "data" / "engines")


@dataclass
class InstancesConfig:
    """Configuration file format for instances."""

    instances: list[InstanceConfig]

    def __post_init__(self) -> None:
        """Validate that all instance IDs are unique."""
        names = [instance.name for instance in self.instances]
        if len(names) != len(set(names)):
            duplicates = [n for n in set(names) if names.count(n) > 1]
            raise ValueError(f"Duplicate instance names found: {duplicates}")


__all__ = [
    "InstanceConfig",
    "InstancesConfig",
    "InstanceType",
]
