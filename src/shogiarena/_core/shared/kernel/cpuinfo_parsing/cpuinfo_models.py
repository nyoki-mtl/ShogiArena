from __future__ import annotations

from typing import TypedDict


class CpuInfoDict(TypedDict, total=False):
    arch: str
    arch_string_raw: str
    vendor_id_raw: str | None
    brand_raw: str | None
    family: int | None
    model: int | None
    flags: list[str]
    system: str


def build_base_cpu_info(arch: str, arch_string_raw: str) -> CpuInfoDict:
    return {
        "arch": arch,
        "arch_string_raw": arch_string_raw,
        "vendor_id_raw": None,
        "brand_raw": None,
        "family": None,
        "model": None,
        "flags": [],
    }


__all__ = ["CpuInfoDict", "build_base_cpu_info"]
