"""CPU target detection combining platform probe with mapping logic."""

from __future__ import annotations

from shogiarena._core.platform.host_probe.platform_detectors import get_cpu_info
from shogiarena._core.shared.kernel.cpuinfo_parsing.mapping import map_info_to_target_cpu


def detect_target_cpu() -> str:
    """Detect the local TARGET_CPU string by probing the host and mapping."""

    return map_info_to_target_cpu(get_cpu_info())


__all__ = ["detect_target_cpu"]
