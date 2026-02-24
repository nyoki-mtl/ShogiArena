from __future__ import annotations

import logging
import platform
import re
import subprocess
from typing import TypedDict

logger = logging.getLogger(__name__)

# Lightweight vendored CPU info (trimmed for our needs):
# - No dynamic CPUID execution (avoids Rosetta/SELinux issues)
# - OS-level sources only (Linux: /proc/cpuinfo, macOS: sysctl, Windows: registry)
# - Returns a stable subset: arch, arch_string_raw, vendor_id_raw, brand_raw, family, model, flags


class CpuInfoDict(TypedDict, total=False):
    arch: str
    arch_string_raw: str
    vendor_id_raw: str | None
    brand_raw: str | None
    family: int | None
    model: int | None
    flags: list[str]
    system: str


def _normalize_arch(arch_string_raw: str | None) -> tuple[str, str]:
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


def _norm_flag_token(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    sl = s.lower().replace(".", "_")
    # common apple tokens
    if sl == "avx1_0":
        return "avx"
    if sl == "avx2_0":
        return "avx2"
    return sl


def _normalize_flags(flags: str | list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    if isinstance(flags, list) or isinstance(flags, tuple):
        for f in flags:
            if not isinstance(f, str):
                continue
            t = _norm_flag_token(f)
            if t:
                out.append(t)
    out = sorted(set(out))
    # alias
    if "avxvnni" in out and "avx_vnni" not in out:
        out.append("avx_vnni")
    return sorted(set(out))


def _to_int_or_none(v: object) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip()
        if not s:
            return None
        return int(s)
    except (ValueError, TypeError):
        return None


def parse_linux_cpuinfo_text(text: str) -> CpuInfoDict:
    arch, raw = _normalize_arch(platform.machine())
    info: CpuInfoDict = {
        "arch": arch,
        "arch_string_raw": raw,
        "vendor_id_raw": None,
        "brand_raw": None,
        "family": None,
        "model": None,
        "flags": [],
    }
    section = text.split("\n\n", 1)[0]
    flags: list[str] = []
    for line in section.splitlines():
        if ":" not in line:
            continue
        try:
            k, v = [x.strip() for x in line.split(":", 1)]
        except ValueError:
            # Skip malformed line
            continue
        lk = k.lower()
        if lk == "vendor_id":
            info["vendor_id_raw"] = v
        elif lk == "model name":
            info["brand_raw"] = v
        elif lk == "cpu family":
            info["family"] = _to_int_or_none(v)
        elif lk == "model":
            info["model"] = _to_int_or_none(v)
        elif lk in ("flags", "features"):
            flags.extend(v.split())
    info["flags"] = _normalize_flags(flags)
    return info


def _linux_info() -> CpuInfoDict:
    arch, raw = _normalize_arch(platform.machine())
    info: CpuInfoDict = {
        "arch": arch,
        "arch_string_raw": raw,
        "vendor_id_raw": None,
        "brand_raw": None,
        "family": None,
        "model": None,
        "flags": [],
    }
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        parsed = parse_linux_cpuinfo_text(text)
        info["arch"] = parsed.get("arch", info["arch"])
        info["arch_string_raw"] = parsed.get("arch_string_raw", info["arch_string_raw"])
        info["vendor_id_raw"] = parsed.get("vendor_id_raw", info["vendor_id_raw"])
        info["brand_raw"] = parsed.get("brand_raw", info["brand_raw"])
        info["family"] = parsed.get("family", info["family"])
        info["model"] = parsed.get("model", info["model"])
        info["flags"] = parsed.get("flags", info["flags"])
    except FileNotFoundError as e:
        logger.error("/proc/cpuinfo not found: %s", e)
    except OSError as e:
        logger.error("Failed to read /proc/cpuinfo: %s", e)
    return info


def _macos_info() -> CpuInfoDict:
    arch, raw = _normalize_arch(platform.machine())
    info: CpuInfoDict = {
        "arch": arch,
        "arch_string_raw": raw,
        "vendor_id_raw": None,
        "brand_raw": None,
        "family": None,
        "model": None,
        "flags": [],
    }

    def sysctl_get(name: str) -> str:
        try:
            out = subprocess.check_output(["sysctl", "-n", name], text=True).strip()
            return out
        except subprocess.CalledProcessError as e:
            logger.error("sysctl failed for %s: %s", name, e)
            raise

    # Core fields
    try:
        info["vendor_id_raw"] = sysctl_get("machdep.cpu.vendor")
        info["brand_raw"] = sysctl_get("machdep.cpu.brand_string")
        info["family"] = _to_int_or_none(sysctl_get("machdep.cpu.family"))
        info["model"] = _to_int_or_none(sysctl_get("machdep.cpu.model"))
    except subprocess.CalledProcessError:
        # Keep None; continue to collect flags
        pass

    # Feature flags
    feats: list[str] = []
    for key in ("machdep.cpu.features", "machdep.cpu.leaf7_features", "machdep.cpu.extfeatures"):
        try:
            s = sysctl_get(key)
            feats.extend(s.split())
        except subprocess.CalledProcessError as e:
            logger.debug("sysctl key missing %s: %s", key, e)
            continue
    info["flags"] = _normalize_flags(feats)
    return info


def _windows_info() -> CpuInfoDict:
    arch, raw = _normalize_arch(platform.machine())
    info: CpuInfoDict = {
        "arch": arch,
        "arch_string_raw": raw,
        "vendor_id_raw": None,
        "brand_raw": None,
        "family": None,
        "model": None,
        "flags": [],
    }
    try:
        import importlib

        winreg = importlib.import_module("winreg")

        def qkey(path: str, name: str) -> str | None:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                val = winreg.QueryValueEx(k, name)[0]
                winreg.CloseKey(k)
                return str(val)
            except FileNotFoundError as e:
                logger.debug("Registry key not found %s/%s: %s", path, name, e)
                return None
            except OSError as e:
                logger.warning("Registry read failed %s/%s: %s", path, name, e)
                return None

        info["vendor_id_raw"] = qkey(r"Hardware\\Description\\System\\CentralProcessor\\0", "VendorIdentifier")
        info["brand_raw"] = qkey(r"Hardware\\Description\\System\\CentralProcessor\\0", "ProcessorNameString")
        ident = qkey(r"Hardware\\Description\\System\\CentralProcessor\\0", "Identifier")
        if ident:
            m = re.search(r"Family\s+(\d+).*Model\s+(\d+)", ident)
            if m:
                info["family"] = _to_int_or_none(m.group(1))
                info["model"] = _to_int_or_none(m.group(2))

        feature_bits_raw = qkey(r"Hardware\\Description\\System\\CentralProcessor\\0", "FeatureSet")
        if feature_bits_raw is not None:
            try:
                feature_bits = int(feature_bits_raw)

                def is_set(bit: int) -> bool:
                    mask = 0x80000000 >> bit
                    return (feature_bits & mask) > 0

                flags_map = {"sse": is_set(25), "sse2": is_set(26)}
                flags = [k for k, v in flags_map.items() if v]
                info["flags"] = _normalize_flags(flags)
            except ValueError as e:
                logger.warning("Invalid registry FeatureSet: %s", e)

        # Augment via IsProcessorFeaturePresent for reliable AVX/AVX2 detection
        try:
            import ctypes

            windll = getattr(ctypes, "windll", None)
            if windll is None:
                raise RuntimeError("ctypes.windll unavailable")
            kernel32 = getattr(windll, "kernel32", None)
            if kernel32 is None:
                raise RuntimeError("kernel32 unavailable via ctypes.windll")
            k32 = kernel32

            def _pf(n: int) -> bool:
                try:
                    return bool(k32.IsProcessorFeaturePresent(n))
                except Exception as e:
                    logger.debug("IsProcessorFeaturePresent(%d) failed: %s", n, e)
                    return False

            PF_XMMI64_INSTRUCTIONS_AVAILABLE = 10  # SSE2
            PF_SSE3_INSTRUCTIONS_AVAILABLE = 13  # SSE3
            PF_AVX_INSTRUCTIONS_AVAILABLE = 18  # AVX
            PF_AVX2_INSTRUCTIONS_AVAILABLE = 19  # AVX2

            aug: list[str] = []
            if _pf(PF_XMMI64_INSTRUCTIONS_AVAILABLE):
                aug.append("sse2")
            if _pf(PF_SSE3_INSTRUCTIONS_AVAILABLE):
                aug.append("sse3")
            if _pf(PF_AVX_INSTRUCTIONS_AVAILABLE):
                aug.append("avx")
            if _pf(PF_AVX2_INSTRUCTIONS_AVAILABLE):
                aug.append("avx2")
            if aug:
                merged = list({*info.get("flags", []), *aug})
                info["flags"] = _normalize_flags(merged)
        except (AttributeError, OSError, RuntimeError) as e:
            logger.debug("Windows PF detection unavailable: %s", e)
    except ModuleNotFoundError as e:
        logger.error("winreg not available: %s", e)
    return info


def get_cpu_info() -> CpuInfoDict:
    sysname = platform.system().lower()
    if sysname == "linux":
        info = _linux_info()
    elif sysname == "darwin":
        info = _macos_info()
    elif sysname == "windows":
        info = _windows_info()
    else:
        # Fallback minimal
        arch, raw = _normalize_arch(platform.machine())
        info: CpuInfoDict = {
            "arch": arch,
            "arch_string_raw": raw,
            "vendor_id_raw": None,
            "brand_raw": None,
            "family": None,
            "model": None,
            "flags": [],
        }

    # Attempt CPUID-based augmentation on x86 (adds avx2/avx512/*vnni when available)
    try:
        _augment_flags_with_cpuid(info)
    except (OSError, RuntimeError, ValueError) as e:
        logger.debug("CPUID augmentation skipped: %s", e)
    return info


# ----------------------- Optional CPUID augmentation (x86) -----------------------


def _is_x86_64(arch_raw: str) -> bool:
    s = arch_raw.lower()
    return s in {"x86_64", "amd64"}


def _run_asm_windows(machine_code: bytes) -> int:
    import ctypes

    size = len(machine_code)
    if size < 0x1000:
        size = 0x1000
    MEM_COMMIT = 0x1000
    PAGE_READWRITE = 0x04
    PAGE_EXECUTE = 0x10

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise OSError("ctypes.windll unavailable")
    k32 = getattr(windll, "kernel32", None)
    if k32 is None:
        raise OSError("kernel32 unavailable via ctypes.windll")

    VirtualAlloc = k32.VirtualAlloc
    VirtualAlloc.restype = ctypes.c_void_p
    addr = VirtualAlloc(None, ctypes.c_size_t(size), MEM_COMMIT, PAGE_READWRITE)
    if not addr:
        raise OSError("VirtualAlloc failed")

    # Copy code using ctypes.memmove
    ctypes.memmove(ctypes.c_void_p(addr), machine_code, len(machine_code))

    VirtualProtect = k32.VirtualProtect
    old_protect = ctypes.c_ulong(0)
    if not VirtualProtect(ctypes.c_void_p(addr), ctypes.c_size_t(size), PAGE_EXECUTE, ctypes.byref(old_protect)):
        raise OSError("VirtualProtect failed")

    GetCurrentProcess = k32.GetCurrentProcess
    GetCurrentProcess.restype = ctypes.c_void_p
    hproc = ctypes.c_void_p(GetCurrentProcess())
    FlushInstructionCache = k32.FlushInstructionCache
    if not FlushInstructionCache(hproc, ctypes.c_void_p(addr), ctypes.c_size_t(size)):
        raise OSError("FlushInstructionCache failed")

    functype = ctypes.CFUNCTYPE(ctypes.c_uint32)
    func = functype(addr)
    try:
        return int(func())
    finally:
        VirtualFree = k32.VirtualFree
        MEM_RELEASE = 0x8000
        VirtualFree(ctypes.c_void_p(addr), ctypes.c_size_t(0), MEM_RELEASE)


def _run_asm_posix(machine_code: bytes) -> int:
    import ctypes
    from mmap import MAP_ANONYMOUS, MAP_PRIVATE, PROT_EXEC, PROT_READ, PROT_WRITE, mmap

    mm = mmap(-1, len(machine_code), flags=MAP_PRIVATE | MAP_ANONYMOUS, prot=PROT_WRITE | PROT_READ | PROT_EXEC)
    try:
        mm.write(machine_code)
        addr = ctypes.addressof(ctypes.c_int.from_buffer(mm))
        functype = ctypes.CFUNCTYPE(ctypes.c_uint32)
        func = functype(addr)
        return int(func())
    finally:
        mm.close()


def _cpuid_leaf7_regs() -> tuple[int, int, int]:
    """Return (EBX, ECX, EDX) for CPUID leaf=7, subleaf=0 on x86.

    Uses small machine code snippets (py-cpuinfo approach) with RWX memory.
    May raise OSError on allocation/exec failures.
    """
    mc_ebx = (
        b"\xb8\x07\x00\x00\x00"  # mov eax,7
        b"\xb9\x00\x00\x00\x00"  # mov ecx,0
        b"\x0f\xa2"  # cpuid
        b"\x89\xd8"  # mov eax,ebx
        b"\xc3"  # ret
    )
    mc_ecx = (
        b"\xb8\x07\x00\x00\x00"  # mov eax,7
        b"\xb9\x00\x00\x00\x00"  # mov ecx,0
        b"\x0f\xa2"  # cpuid
        b"\x89\xc8"  # mov eax,ecx
        b"\xc3"  # ret
    )
    mc_edx = (
        b"\xb8\x07\x00\x00\x00"  # mov eax,7
        b"\xb9\x00\x00\x00\x00"  # mov ecx,0
        b"\x0f\xa2"  # cpuid
        b"\x89\xd0"  # mov eax,edx
        b"\xc3"  # ret
    )

    sysname = platform.system().lower()
    if sysname == "windows":
        ebx = _run_asm_windows(mc_ebx)
        ecx = _run_asm_windows(mc_ecx)
        edx = _run_asm_windows(mc_edx)
    else:
        ebx = _run_asm_posix(mc_ebx)
        ecx = _run_asm_posix(mc_ecx)
        edx = _run_asm_posix(mc_edx)
    return ebx, ecx, edx


def _augment_flags_with_cpuid(info: CpuInfoDict) -> None:
    """Augment flags with CPUID leaf7 detection for x86.

    Adds: avx2, avx512f, avx512_vnni/avx512vnni, avx_vnni where supported.
    Safe no-op on non-x86 architectures or when execution is denied.
    """
    arch_raw = str(info.get("arch_string_raw") or platform.machine())
    if not _is_x86_64(arch_raw):
        return
    try:
        ebx, ecx, _edx = _cpuid_leaf7_regs()
    except (OSError, RuntimeError, ValueError) as e:
        logger.debug("CPUID leaf7 unavailable: %s", e)
        return

    def setbit(val: int, bit: int) -> bool:
        return ((val >> bit) & 1) == 1

    add: list[str] = []
    if setbit(ebx, 5):
        add.append("avx2")
    if setbit(ebx, 16):
        add.append("avx512f")
    if setbit(ecx, 11):
        add.extend(["avx512_vnni", "avx512vnni"])  # allow both spellings
    if setbit(ecx, 4):
        add.append("avx_vnni")

    if add:
        merged = list({*info.get("flags", []), *add})
        info["flags"] = _normalize_flags(merged)


def _normalize_vendor(vendor: str | None) -> str:
    v = (vendor or "").strip().lower()
    if not v:
        return ""
    if "amd" in v:
        return "amd"
    if "intel" in v:
        return "intel"
    return v


def map_info_to_target_cpu(info: CpuInfoDict) -> str:
    """Map parsed CPU info to YaneuraOu TARGET_CPU string."""
    sys_name = str(info.get("system") or platform.system()).lower()
    arch_raw = (info.get("arch_string_raw") or platform.machine()).lower()

    # macOS
    if sys_name == "darwin":
        if arch_raw in ("arm64", "aarch64"):
            return "APPLEM1"
        fl = {x.lower() for x in info.get("flags", [])}
        if "avx2" in fl:
            return "APPLEAVX2"
        if "sse4_2" in fl:
            return "APPLESSE42"
        return "APPLESSE42"

    # ARM64
    if arch_raw in ("arm64", "aarch64"):
        brand = (info.get("brand_raw") or "").lower()
        if "graviton" in brand:
            return "GRAVITON2"
        return "OTHER"

    vendor = _normalize_vendor(info.get("vendor_id_raw"))
    family = _to_int_or_none(info.get("family"))
    model = _to_int_or_none(info.get("model"))
    fl = {x.lower() for x in info.get("flags", [])}

    # AMD Zen family mapping
    if vendor == "amd" and family is not None and model is not None:
        if family == 25 and 1 <= model <= 127:
            return "ZEN3"
        if family == 23 and 49 <= model <= 255:
            return "ZEN2"
        if family == 23 and (1 <= model <= 8 or 16 <= model <= 23):
            return "ZEN1"

    # Feature-based detection
    if any(x in fl for x in ("avx512_vnni", "avx512vnni")):
        return "AVX512VNNI"
    if "avx512f" in fl:
        return "AVX512"
    if "avx_vnni" in fl or "avxvnni" in fl:
        return "AVXVNNI"
    if "avx2" in fl:
        return "AVX2"
    if "sse4_2" in fl:
        return "SSE42"
    if "sse4_1" in fl:
        return "SSE41"

    logger.warning("CPU features not detected; defaulting TARGET_CPU=SSE42")
    return "SSE42"


def detect_target_cpu() -> str:
    """Detect a reasonable YaneuraOu TARGET_CPU for this machine.

    Mapping follows YaneuraOu Makefile docs and the provided Go example logic.
    """
    info = get_cpu_info()
    return map_info_to_target_cpu(info)
