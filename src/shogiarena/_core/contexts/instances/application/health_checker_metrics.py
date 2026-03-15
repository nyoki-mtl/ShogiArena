"""Metric parsing and platform-specific collectors for instance health checks."""

import asyncio
import logging
import re
from typing import TypedDict

import psutil

from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport

from .instance_models import InstanceMetrics

logger = logging.getLogger(__name__)


class _MpstatData(TypedDict):
    overall: float | None
    per_core: list[float] | None


async def terminate_subprocess(proc: asyncio.subprocess.Process, label: str) -> None:
    """Best-effort terminate a subprocess to avoid leaks after timeouts."""
    try:
        proc.kill()
    except ProcessLookupError:
        return
    except (OSError, RuntimeError) as exc:
        logger.debug("Failed to kill %s process: %s", label, exc)
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (TimeoutError, OSError, RuntimeError) as exc:
        logger.debug("Timed out waiting for %s process to exit: %s", label, exc)


async def collect_local_mpstat() -> _MpstatData | None:
    """Collect per-core CPU usage using mpstat on the local machine."""

    try:
        proc = await asyncio.create_subprocess_exec(
            "mpstat",
            "-P",
            "ALL",
            "1",
            "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
    except TimeoutError:
        logger.debug("mpstat local run timed out")
        await terminate_subprocess(proc, "mpstat local")
        return None
    if proc.returncode != 0:
        logger.debug("mpstat local failed rc=%s: %s", proc.returncode, stderr.decode("utf-8", errors="ignore"))
        return None
    return _parse_mpstat_output(stdout.decode("utf-8", errors="ignore"))


async def collect_remote_mpstat(transport: SshTransport) -> _MpstatData | None:
    """Collect per-core CPU usage via mpstat on a remote instance."""

    try:
        rc, out, err = await transport.run("mpstat -P ALL 1 1", timeout=20.0)
    except (TimeoutError, OSError, RuntimeError) as exc:
        logger.debug("mpstat remote error: %s", exc)
        return None
    if rc != 0:
        logger.debug("mpstat remote failed rc=%s: %s", rc, err.strip())
        return None
    return _parse_mpstat_output(out)


def _parse_mpstat_output(output: str) -> _MpstatData | None:
    """Parse ``mpstat`` output into overall and per-core utilisation values."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    per_cpu_usage: dict[str, float] = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        cpu_label = parts[1]
        if cpu_label.lower() == "cpu":
            continue
        if cpu_label.lower() in {"avg", "average:"}:
            if len(parts) >= 4:
                cpu_label = parts[2]
                parts = parts[2:]
            else:
                continue
        try:
            idle = float(parts[-1])
        except ValueError:
            logger.debug("Skipping mpstat row with non-numeric idle value: %s", line)
            continue
        usage = round(max(0.0, 100.0 - idle), 2)
        per_cpu_usage[cpu_label.lower()] = usage
    if not per_cpu_usage:
        return None
    overall = per_cpu_usage.pop("all", None)
    if overall is None and per_cpu_usage:
        overall = round(sum(per_cpu_usage.values()) / len(per_cpu_usage), 2)
    per_core: list[float] = []
    if per_cpu_usage:
        try:
            per_core = [per_cpu_usage[key] for key in sorted(per_cpu_usage, key=lambda x: int(x))]
        except ValueError:
            logger.debug("mpstat CPU labels are not numeric; preserving raw order")
            per_core = list(per_cpu_usage.values())
    return {"overall": overall, "per_core": per_core or None}


def apply_mpstat(metrics: InstanceMetrics, mpstat_data: _MpstatData) -> None:
    """Apply mpstat-derived CPU usage to the metrics object."""

    overall = mpstat_data.get("overall")
    if isinstance(overall, float | int):
        metrics.cpu_usage_pct = float(overall)
    per_core = mpstat_data.get("per_core")
    if isinstance(per_core, list) and per_core:
        normalized_per_core = [float(v) for v in per_core if isinstance(v, int | float)]
        if normalized_per_core:
            metrics.cpu_usage_pct_per_core = normalized_per_core
            if metrics.cpu_count is None:
                metrics.cpu_count = len(normalized_per_core)


def windows_fill_metrics_psutil(metrics: InstanceMetrics) -> bool:
    """Fill Windows CPU/memory metrics using psutil when available."""
    is_filled = False
    try:
        mem = psutil.virtual_memory()
        metrics.mem_total_mb = int(mem.total // (1024 * 1024))
        metrics.mem_used_mb = int(mem.used // (1024 * 1024))
        metrics.mem_free_mb = int(mem.available // (1024 * 1024))
        if metrics.mem_total_mb:
            metrics.mem_used_pct = round((metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1)
        is_filled = True
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("psutil memory metrics failed: %s", exc, exc_info=True)

    try:
        metrics.cpu_usage_pct = float(psutil.cpu_percent(interval=0.1))
        is_filled = True
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("psutil cpu metrics failed: %s", exc, exc_info=True)

    return is_filled


async def windows_fill_memory_metrics(metrics: InstanceMetrics) -> None:
    """Fill memory metrics on Windows using WMIC then PowerShell fallback."""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "wmic",
            "OS",
            "get",
            "TotalVisibleMemorySize,FreePhysicalMemory",
            "/Value",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        text = out.decode("utf-8", errors="ignore")
        vals: dict[str, int] = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    vals[k.strip()] = int(v.strip())
                except ValueError:
                    logger.debug("Ignoring WMIC memory value with non-integer payload: %s=%s", k.strip(), v.strip())
        total_kb = vals.get("TotalVisibleMemorySize")
        free_kb = vals.get("FreePhysicalMemory")
        if total_kb:
            metrics.mem_total_mb = total_kb // 1024
        if free_kb and total_kb:
            metrics.mem_free_mb = free_kb // 1024
            if metrics.mem_total_mb is not None:
                metrics.mem_used_mb = metrics.mem_total_mb - metrics.mem_free_mb
                if metrics.mem_total_mb:
                    metrics.mem_used_pct = round((metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1)
        return
    except TimeoutError:
        if proc is not None and proc.returncode is None:
            await terminate_subprocess(proc, "wmic memory")
        logger.debug("WMIC memory query timed out")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("WMIC memory query failed: %s", exc)

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$os = Get-CimInstance Win32_OperatingSystem;"
                'Write-Output ("$($os.TotalVisibleMemorySize) $($os.FreePhysicalMemory)")'
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        text = out.decode("utf-8", errors="ignore").strip()
        parts = text.split()
        if len(parts) >= 2:
            try:
                total_kb = int(parts[0])
                free_kb = int(parts[1])
                metrics.mem_total_mb = total_kb // 1024
                metrics.mem_free_mb = free_kb // 1024
                if metrics.mem_total_mb is not None:
                    metrics.mem_used_mb = metrics.mem_total_mb - metrics.mem_free_mb
                    if metrics.mem_total_mb:
                        metrics.mem_used_pct = round((metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1)
            except ValueError:
                logger.debug("PowerShell memory output is not parseable as integers: %s", text)
    except TimeoutError:
        if proc is not None and proc.returncode is None:
            await terminate_subprocess(proc, "powershell memory")
        logger.debug("PowerShell memory query timed out")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("PowerShell memory query failed: %s", exc)


async def windows_fill_cpu_metrics(metrics: InstanceMetrics) -> None:
    """Fill CPU usage metrics on Windows using WMIC then PowerShell fallback."""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "wmic",
            "CPU",
            "get",
            "LoadPercentage",
            "/Value",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        text = out.decode("utf-8", errors="ignore")
        loads: list[float] = []
        for line in text.splitlines():
            if line.lower().startswith("loadpercentage="):
                try:
                    loads.append(float(line.split("=", 1)[1].strip()))
                except ValueError:
                    logger.debug("Ignoring WMIC CPU load row with non-numeric value: %s", line)
        if loads:
            metrics.cpu_usage_pct = sum(loads) / len(loads)
            return
    except TimeoutError:
        if proc is not None and proc.returncode is None:
            await terminate_subprocess(proc, "wmic cpu")
        logger.debug("WMIC CPU load query timed out")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("WMIC CPU load query failed: %s", exc)

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "(Get-CimInstance Win32_Processor |"
                " Select-Object -ExpandProperty LoadPercentage) |"
                " Measure-Object -Average |"
                " Select-Object -ExpandProperty Average"
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        text = out.decode("utf-8", errors="ignore").strip()
        try:
            metrics.cpu_usage_pct = float(text)
        except ValueError:
            logger.debug("PowerShell CPU output is not parseable as float: %s", text)
    except TimeoutError:
        if proc is not None and proc.returncode is None:
            await terminate_subprocess(proc, "powershell cpu")
        logger.debug("PowerShell CPU load query timed out")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("PowerShell CPU load query failed: %s", exc)


def parse_system_output(output: str) -> InstanceMetrics:
    """
    Parse system command output to extract metrics.

    Expected format from 'uptime; nproc; free -m; top -bn1 | head -5':
    - Line 1: uptime with load averages
    - Line 2: CPU count from nproc
    - Line 3+: free memory output (header + values)
    - Remaining: top output with CPU usage

    Args:
        output: Combined output from system commands

    Returns:
        Parsed InstanceMetrics
    """
    lines = [line.strip() for line in output.split("\n") if line.strip()]
    metrics = InstanceMetrics(is_reachable=True)

    try:
        if len(lines) < 2:
            logger.warning("Insufficient output lines for health check parsing")
            return metrics

        uptime_line = lines[0]
        load_match = re.search(r"load average:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)", uptime_line)
        if load_match:
            metrics.load_avg_1 = float(load_match.group(1))
            metrics.load_avg_5 = float(load_match.group(2))
            metrics.load_avg_15 = float(load_match.group(3))

        if len(lines) >= 2 and lines[1].isdigit():
            metrics.cpu_count = int(lines[1])

        for line in lines[2:]:
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 7:
                    try:
                        metrics.mem_total_mb = int(parts[1])
                        metrics.mem_used_mb = int(parts[2])
                        metrics.mem_free_mb = int(parts[3])
                        if metrics.mem_total_mb:
                            metrics.mem_used_pct = round(
                                (metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100,
                                1,
                            )
                    except (ValueError, IndexError):
                        logger.debug("Could not parse memory info from free output")
                break

        for line in lines:
            cpu_match = re.search(r"%?[Cc]pu\(s\):\s*([0-9.]+)%?\s*us", line)
            if cpu_match:
                metrics.cpu_usage_pct = float(cpu_match.group(1))
                break

        for line in lines:
            if "model name" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    metrics.cpu_model = parts[1].strip()
                else:
                    metrics.cpu_model = line.strip()
                break

    except (ValueError, TypeError, IndexError) as exc:
        logger.debug("Error parsing health check output: %s", exc)

    return metrics
