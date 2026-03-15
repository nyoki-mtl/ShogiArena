from __future__ import annotations

import logging
import platform

from shogiarena._core.shared.kernel.cpuinfo_parsing.cpuinfo_models import CpuInfoDict
from shogiarena._core.shared.kernel.cpuinfo_parsing.normalization import normalize_vendor
from shogiarena._core.shared.kernel.scalar_coercion.numeric import coerce_int

logger = logging.getLogger(__name__)


def map_info_to_target_cpu(info: CpuInfoDict) -> str:
    """Map parsed CPU info to YaneuraOu TARGET_CPU string."""

    sys_name = str(info.get("system") or platform.system()).lower()
    arch_raw = (info.get("arch_string_raw") or platform.machine()).lower()

    if sys_name == "darwin":
        if arch_raw in ("arm64", "aarch64"):
            return "APPLEM1"
        flags = {x.lower() for x in info.get("flags", [])}
        if "avx2" in flags:
            return "APPLEAVX2"
        if "sse4_2" in flags:
            return "APPLESSE42"
        return "APPLESSE42"

    if arch_raw in ("arm64", "aarch64"):
        brand = (info.get("brand_raw") or "").lower()
        if "graviton" in brand:
            return "GRAVITON2"
        return "OTHER"

    vendor = normalize_vendor(info.get("vendor_id_raw"))
    family = coerce_int(info.get("family"))
    model = coerce_int(info.get("model"))
    flags = {x.lower() for x in info.get("flags", [])}

    if vendor == "amd" and family is not None and model is not None:
        if family == 25 and 1 <= model <= 127:
            return "ZEN3"
        if family == 23 and 49 <= model <= 255:
            return "ZEN2"
        if family == 23 and (1 <= model <= 8 or 16 <= model <= 23):
            return "ZEN1"

    if any(item in flags for item in ("avx512_vnni", "avx512vnni")):
        return "AVX512VNNI"
    if "avx512f" in flags:
        return "AVX512"
    if "avx_vnni" in flags or "avxvnni" in flags:
        return "AVXVNNI"
    if "avx2" in flags:
        return "AVX2"
    if "sse4_2" in flags:
        return "SSE42"
    if "sse4_1" in flags:
        return "SSE41"

    logger.warning("CPU features not detected; defaulting TARGET_CPU=SSE42")
    return "SSE42"


__all__ = ["map_info_to_target_cpu"]
