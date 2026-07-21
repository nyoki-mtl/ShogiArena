from __future__ import annotations

from shogiarena._core.contexts.instances.application.ssh_transport import create_transport
from shogiarena._core.shared.kernel.cpuinfo_parsing.mapping import map_info_to_target_cpu
from shogiarena._core.shared.kernel.cpuinfo_parsing.parser import parse_linux_cpuinfo_text

from .instance_models import Instance, InstanceType

_CPU_CACHE: dict[str, str] = {}


async def detect_remote_target_cpu(instance: Instance) -> str:
    """Detect TARGET_CPU on a remote SSH instance using /proc/cpuinfo.

    Raises on failure (no fallback), per project error policy.
    """
    if instance.config.type != InstanceType.SSH:
        raise ValueError("detect_remote_target_cpu requires SSH instance")
    # cache by instance name
    key = instance.name
    if key in _CPU_CACHE:
        return _CPU_CACHE[key]
    t = create_transport(instance)
    await t.connect()
    try:
        rc1, text, err1 = await t.run("cat /proc/cpuinfo")
        if rc1 != 0:
            raise RuntimeError(f"Failed to read /proc/cpuinfo on remote: {err1}")
        rc2, arch, err2 = await t.run("uname -m")
        if rc2 != 0:
            raise RuntimeError(f"Failed to read arch via uname -m on remote: {err2}")
        arch = arch.strip()
        info = parse_linux_cpuinfo_text(text)
        if not info or not isinstance(info, dict):
            raise RuntimeError("Failed to parse remote /proc/cpuinfo")
        if arch:
            info["arch_string_raw"] = arch
        info["system"] = "linux"
        cpu = map_info_to_target_cpu(info)
        _CPU_CACHE[key] = cpu
        return cpu
    finally:
        await t.close()
