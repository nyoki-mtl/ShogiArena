from __future__ import annotations

import platform

from shogiarena._core.shared.kernel.cpuinfo_parsing.cpuinfo_models import CpuInfoDict, build_base_cpu_info
from shogiarena._core.shared.kernel.cpuinfo_parsing.normalization import (
    normalize_arch,
    normalize_flags,
)
from shogiarena._core.shared.kernel.scalar_coercion.numeric import coerce_int


def parse_linux_cpuinfo_text(text: str) -> CpuInfoDict:
    arch, raw = normalize_arch(platform.machine())
    info = build_base_cpu_info(arch=arch, arch_string_raw=raw)
    section = text.split("\n\n", 1)[0]
    flags: list[str] = []
    for line in section.splitlines():
        if ":" not in line:
            continue
        try:
            key, value = [x.strip() for x in line.split(":", 1)]
        except ValueError:
            continue
        lower_key = key.lower()
        if lower_key == "vendor_id":
            info["vendor_id_raw"] = value
        elif lower_key == "model name":
            info["brand_raw"] = value
        elif lower_key == "cpu family":
            info["family"] = coerce_int(value)
        elif lower_key == "model":
            info["model"] = coerce_int(value)
        elif lower_key in ("flags", "features"):
            flags.extend(value.split())
    info["flags"] = normalize_flags(flags)
    return info


__all__ = ["parse_linux_cpuinfo_text"]
