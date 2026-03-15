from __future__ import annotations

import platform
import re


def normalize_arch(arch_string_raw: str | None) -> tuple[str, str]:
    raw = (arch_string_raw or platform.machine() or "").strip()
    raw_l = raw.lower()
    if re.match(r"^(x86_64|amd64)$", raw_l):
        return ("X86_64", raw)
    if re.match(r"^(i386|i686|x86)$", raw_l):
        return ("X86_32", raw)
    if re.match(r"^(aarch64|arm64)$", raw_l):
        return ("ARM_8", raw)
    if re.match(r"^armv7|^armv6|^armv8$", raw_l):
        return ("ARM_7", raw)
    if re.match(r"^loongarch64$", raw_l):
        return ("LOONG_64", raw)
    if re.match(r"^loongarch32$", raw_l):
        return ("LOONG_32", raw)
    if re.match(r"^riscv64$", raw_l):
        return ("RISCV_64", raw)
    if re.match(r"^riscv(32|32be)$", raw_l):
        return ("RISCV_32", raw)
    return (raw.upper(), raw)


def _norm_flag_token(value: str) -> str:
    normalized = value.strip().lower().replace(".", "_")
    if not normalized:
        return ""
    if normalized == "avx1_0":
        return "avx"
    if normalized == "avx2_0":
        return "avx2"
    return normalized


def normalize_flags(flags: str | list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    if isinstance(flags, list | tuple):
        for item in flags:
            if not isinstance(item, str):
                continue
            token = _norm_flag_token(item)
            if token:
                out.append(token)
    out = sorted(set(out))
    if "avxvnni" in out and "avx_vnni" not in out:
        out.append("avx_vnni")
    return sorted(set(out))


def normalize_vendor(vendor: str | None) -> str:
    normalized = (vendor or "").strip().lower()
    if not normalized:
        return ""
    if "amd" in normalized:
        return "amd"
    if "intel" in normalized:
        return "intel"
    return normalized


__all__ = [
    "normalize_arch",
    "normalize_flags",
    "normalize_vendor",
]
