from __future__ import annotations

import logging
import platform
import subprocess

from shogiarena._core.shared.kernel.cpuinfo_parsing.cpuinfo_models import CpuInfoDict, build_base_cpu_info
from shogiarena._core.shared.kernel.cpuinfo_parsing.normalization import (
    normalize_arch,
    normalize_flags,
)
from shogiarena._core.shared.kernel.cpuinfo_parsing.parser import parse_linux_cpuinfo_text
from shogiarena._core.shared.kernel.scalar_coercion.numeric import coerce_int

logger = logging.getLogger(__name__)


def _linux_info() -> CpuInfoDict:
    arch, raw = normalize_arch(platform.machine())
    info = build_base_cpu_info(arch=arch, arch_string_raw=raw)
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as handle:
            parsed = parse_linux_cpuinfo_text(handle.read())
        info.update(parsed)
    except OSError as exc:
        logger.debug("Failed to read /proc/cpuinfo: %s", exc)
    return info


def _macos_info() -> CpuInfoDict:
    arch, raw = normalize_arch(platform.machine())
    info = build_base_cpu_info(arch=arch, arch_string_raw=raw)

    def _sysctl(name: str) -> str:
        return subprocess.check_output(["sysctl", "-n", name], text=True).strip()

    try:
        info["vendor_id_raw"] = _sysctl("machdep.cpu.vendor")
        info["brand_raw"] = _sysctl("machdep.cpu.brand_string")
        info["family"] = coerce_int(_sysctl("machdep.cpu.family"))
        info["model"] = coerce_int(_sysctl("machdep.cpu.model"))
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.debug("macOS cpu metadata lookup failed: %s", exc)

    feats: list[str] = []
    for key in ("machdep.cpu.features", "machdep.cpu.leaf7_features", "machdep.cpu.extfeatures"):
        try:
            feats.extend(_sysctl(key).split())
        except (subprocess.CalledProcessError, OSError):
            continue
    info["flags"] = normalize_flags(feats)
    return info


def _windows_info() -> CpuInfoDict:
    arch, raw = normalize_arch(platform.machine())
    return build_base_cpu_info(arch=arch, arch_string_raw=raw)


def get_cpu_info() -> CpuInfoDict:
    sysname = platform.system().lower()
    if sysname == "linux":
        return _linux_info()
    if sysname == "darwin":
        return _macos_info()
    if sysname == "windows":
        return _windows_info()
    arch, raw = normalize_arch(platform.machine())
    return build_base_cpu_info(arch=arch, arch_string_raw=raw)


__all__ = ["get_cpu_info"]
