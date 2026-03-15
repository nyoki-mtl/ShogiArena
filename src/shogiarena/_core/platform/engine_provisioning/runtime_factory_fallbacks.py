"""Fallback instance models for runtime factory local default path."""

from __future__ import annotations


class _FallbackMetrics:
    def __init__(self) -> None:
        self.engine_processes = 0


class _FallbackConfig:
    def __init__(self) -> None:
        self.engine_dir = ""
        self.should_install_requirements = False


class FallbackInstance:
    def __init__(self) -> None:
        self.name = "local"
        self.is_ssh = False
        self.config = _FallbackConfig()
        self.metrics = _FallbackMetrics()
        self.max_engine_capacity = 4

    def add_engine_processes(self, delta: int) -> None:
        self.metrics.engine_processes += int(delta)


__all__ = ["FallbackInstance"]
