"""Health checking and metrics collection for instances.

SSH transport uses asyncssh (mandatory). There is no system ssh/scp fallback.
Host key verification behavior follows instance configuration (strict by default).
"""

import asyncio
import logging
import platform
import re
from typing import Any

from shogiarena.arena.remote.ssh_transport import SshTransport, create_transport

from .models import Instance, InstanceMetrics

logger = logging.getLogger(__name__)


class HealthChecker:
    """
    Performs health checks and collects metrics from instances.

    For SSH instances, uses common Unix commands to gather system information.
    For local instances, uses similar approaches but may use direct system calls in the future.
    """

    @staticmethod
    async def check_instance_health(instance: Instance) -> InstanceMetrics:
        """
        Perform health check on an instance and return updated metrics.

        Args:
            instance: Instance to check

        Returns:
            Updated InstanceMetrics with current system state
        """
        if instance.is_local:
            return await HealthChecker._check_local_health(instance)
        elif instance.is_ssh:
            return await HealthChecker._check_ssh_health(instance)
        else:
            # Unknown instance type, mark as unreachable
            logger.warning(f"Unknown instance type for {instance.name}: {instance.type}")
            return InstanceMetrics(reachable=False)

    @staticmethod
    async def _check_local_health(instance: Instance) -> InstanceMetrics:
        """Check health of local instance using system commands."""
        sysname = platform.system().lower()
        process: asyncio.subprocess.Process | None = None
        try:
            if sysname == "linux":
                # Use Linux commands to gather metrics
                cmd = "uptime; nproc; free -m; top -bn1 | head -5; cat /proc/cpuinfo | grep -m 1 -i 'model name'"
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
                if process.returncode != 0:
                    logger.error(f"Local health check failed with return code {process.returncode}: {stderr.decode()}")
                    return InstanceMetrics(reachable=False)
                output = stdout.decode("utf-8", errors="ignore")
                metrics = HealthChecker._parse_system_output(output)
                mpstat_data = await HealthChecker._collect_local_mpstat()
                if mpstat_data:
                    HealthChecker._apply_mpstat(metrics, mpstat_data)
                return metrics

            elif sysname == "windows":
                metrics = InstanceMetrics(reachable=True)
                # Windows metrics are best-effort: prefer psutil, then fall back to wmic/PowerShell.
                # CPU count from Python
                try:
                    import os

                    metrics.cpu_count = os.cpu_count() or None
                except (OSError, AttributeError, ValueError) as exc:
                    logger.debug("Failed to read cpu_count for %s: %s", instance.name, exc)
                    metrics.cpu_count = None
                filled = HealthChecker._windows_fill_metrics_psutil(metrics)
                if not filled:
                    await HealthChecker._windows_fill_memory_metrics(metrics)
                    await HealthChecker._windows_fill_cpu_metrics(metrics)
                return metrics

            else:
                logger.error(f"Local health check unsupported on this OS: {platform.system()}")
                return InstanceMetrics(reachable=False)

        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                await HealthChecker._terminate_subprocess(process, "local health check")
            logger.error(f"Local health check timed out for {instance.name}")
            return InstanceMetrics(reachable=False)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"Local health check error for {instance.name}: {e}")
            return InstanceMetrics(reachable=False)

    @staticmethod
    async def _check_ssh_health(instance: Instance) -> InstanceMetrics:
        """Check health of SSH instance using asyncssh transport."""
        config = instance.config

        if not config.host or not config.user:
            logger.error(f"SSH instance {instance.name} missing host or user configuration")
            return InstanceMetrics(reachable=False)

        t = create_transport(instance)
        try:
            await t.connect()
            # OS check
            rc_os, out_os, err_os = await t.run("uname -s", timeout=15.0)
            if rc_os != 0:
                logger.error(f"SSH remote OS check failed for {config.host}: {err_os.strip()}")
                return InstanceMetrics(reachable=False)
            remote_os = out_os.strip()
            if remote_os.lower() != "linux":
                logger.error(f"Remote health check unsupported on this OS: {remote_os}")
                return InstanceMetrics(reachable=False)

            # Collect metrics
            cmd = "uptime; nproc; free -m; top -bn1 | head -5; cat /proc/cpuinfo | grep -m 1 -i 'model name'"
            rc, out, err = await t.run(cmd, timeout=30.0)
            if rc != 0:
                logger.error(f"SSH health check failed for {config.host}: {err}")
                return InstanceMetrics(reachable=False)
            metrics = HealthChecker._parse_system_output(out)
            mpstat_data = await HealthChecker._collect_remote_mpstat(t)
            if mpstat_data:
                HealthChecker._apply_mpstat(metrics, mpstat_data)
            return metrics
        except asyncio.TimeoutError:
            logger.error(f"SSH health check timed out for {config.host}")
            return InstanceMetrics(reachable=False)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"SSH health check error for {config.host}: {e}")
            return InstanceMetrics(reachable=False)
        finally:
            try:
                await t.close()
            except (OSError, RuntimeError) as exc:
                logger.debug("Failed to close SSH transport for %s: %s", instance.name, exc)

    @staticmethod
    async def _windows_fill_memory_metrics(metrics: InstanceMetrics) -> None:
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
                        pass
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
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                await HealthChecker._terminate_subprocess(proc, "wmic memory")
            logger.debug("WMIC memory query timed out")
        except (OSError, ValueError, RuntimeError) as e:
            logger.debug(f"WMIC memory query failed: {e}")

        # PowerShell fallback
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
                            metrics.mem_used_pct = round(
                                (metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1
                            )
                except ValueError:
                    pass
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                await HealthChecker._terminate_subprocess(proc, "powershell memory")
            logger.debug("PowerShell memory query timed out")
        except (OSError, ValueError, RuntimeError) as e2:
            logger.debug(f"PowerShell memory query failed: {e2}")

    @staticmethod
    async def _windows_fill_cpu_metrics(metrics: InstanceMetrics) -> None:
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
                        pass
            if loads:
                metrics.cpu_usage_pct = sum(loads) / len(loads)
                return
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                await HealthChecker._terminate_subprocess(proc, "wmic cpu")
            logger.debug("WMIC CPU load query timed out")
        except (OSError, ValueError, RuntimeError) as e:
            logger.debug(f"WMIC CPU load query failed: {e}")

        # PowerShell fallback
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
                pass
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                await HealthChecker._terminate_subprocess(proc, "powershell cpu")
            logger.debug("PowerShell CPU load query timed out")
        except (OSError, ValueError, RuntimeError) as e2:
            logger.debug(f"PowerShell CPU load query failed: {e2}")

    @staticmethod
    def _parse_system_output(output: str) -> InstanceMetrics:
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
        metrics = InstanceMetrics(reachable=True)

        try:
            if len(lines) < 2:
                logger.warning("Insufficient output lines for health check parsing")
                return metrics

            # Parse uptime line for load averages
            # Example: " 12:34:56 up 1 day,  2:34,  3 users,  load average: 0.15, 0.25, 0.35"
            uptime_line = lines[0]
            load_match = re.search(r"load average:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)", uptime_line)
            if load_match:
                metrics.load_avg_1 = float(load_match.group(1))
                metrics.load_avg_5 = float(load_match.group(2))
                metrics.load_avg_15 = float(load_match.group(3))

            # Parse CPU count
            # Line 2 should be nproc output
            if len(lines) >= 2 and lines[1].isdigit():
                metrics.cpu_count = int(lines[1])

            # Parse memory info from free -m output
            # Look for line starting with "Mem:"
            for line in lines[2:]:
                if line.startswith("Mem:"):
                    # Example: "Mem:    32768   8192  12345   1234   12345   12345"
                    parts = line.split()
                    if len(parts) >= 7:
                        try:
                            metrics.mem_total_mb = int(parts[1])
                            metrics.mem_used_mb = int(parts[2])
                            metrics.mem_free_mb = int(parts[3])
                            if metrics.mem_total_mb:
                                metrics.mem_used_pct = round(
                                    (metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1
                                )
                        except (ValueError, IndexError):
                            logger.debug("Could not parse memory info from free output")
                    break

            # Parse CPU usage from top output
            # Look for line with "%Cpu(s):" or similar
            for line in lines:
                cpu_match = re.search(r"%?[Cc]pu\(s\):\s*([0-9.]+)%?\s*us", line)
                if cpu_match:
                    metrics.cpu_usage_pct = float(cpu_match.group(1))
                    break

            # Parse CPU model when available
            for line in lines:
                if "model name" in line.lower():
                    # Example: "model name  : Intel(R) Xeon(R) CPU"
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        metrics.cpu_model = parts[1].strip()
                    else:
                        metrics.cpu_model = line.strip()
                    break

        except (ValueError, TypeError, IndexError) as e:
            logger.debug(f"Error parsing health check output: {e}")
            # Return metrics with reachable=True but potentially incomplete data

        return metrics

    @staticmethod
    def _windows_fill_metrics_psutil(metrics: InstanceMetrics) -> bool:
        """Fill Windows CPU/memory metrics using psutil when available."""
        try:
            import psutil
        except ImportError:
            return False

        filled = False
        try:
            mem = psutil.virtual_memory()
            metrics.mem_total_mb = int(mem.total // (1024 * 1024))
            metrics.mem_used_mb = int(mem.used // (1024 * 1024))
            metrics.mem_free_mb = int(mem.available // (1024 * 1024))
            if metrics.mem_total_mb:
                metrics.mem_used_pct = round((metrics.mem_used_mb or 0) / max(metrics.mem_total_mb, 1) * 100, 1)
            filled = True
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug("psutil memory metrics failed: %s", exc, exc_info=True)

        try:
            metrics.cpu_usage_pct = float(psutil.cpu_percent(interval=0.1))
            filled = True
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug("psutil cpu metrics failed: %s", exc, exc_info=True)

        return filled

    @staticmethod
    async def _collect_local_mpstat() -> dict[str, Any] | None:
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
        except asyncio.TimeoutError:
            logger.debug("mpstat local run timed out")
            await HealthChecker._terminate_subprocess(proc, "mpstat local")
            return None
        if proc.returncode != 0:
            logger.debug("mpstat local failed rc=%s: %s", proc.returncode, stderr.decode("utf-8", errors="ignore"))
            return None
        return HealthChecker._parse_mpstat_output(stdout.decode("utf-8", errors="ignore"))

    @staticmethod
    async def _collect_remote_mpstat(transport: SshTransport) -> dict[str, Any] | None:
        """Collect per-core CPU usage via mpstat on a remote instance."""

        try:
            rc, out, err = await transport.run("mpstat -P ALL 1 1", timeout=20.0)
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            logger.debug("mpstat remote error: %s", exc)
            return None
        if rc != 0:
            logger.debug("mpstat remote failed rc=%s: %s", rc, err.strip())
            return None
        return HealthChecker._parse_mpstat_output(out)

    @staticmethod
    def _parse_mpstat_output(output: str) -> dict[str, Any] | None:
        """Parse ``mpstat`` output into overall and per-core utilisation values."""

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return None
        per_cpu_usage: dict[str, float] = {}
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            # mpstat lines usually start with time token followed by CPU label
            cpu_label = parts[1]
            if cpu_label.lower() == "cpu":
                # header row, skip
                continue
            if cpu_label.lower() in {"avg", "average:"}:
                # handle locales where "Average:" appears
                if len(parts) >= 4:
                    cpu_label = parts[2]
                    parts = parts[2:]
                else:
                    continue
            try:
                idle = float(parts[-1])
            except ValueError:
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
                # sort numerically when the labels are integers
                per_core = [per_cpu_usage[key] for key in sorted(per_cpu_usage, key=lambda x: int(x))]
            except ValueError:
                per_core = list(per_cpu_usage.values())
        return {"overall": overall, "per_core": per_core or None}

    @staticmethod
    def _apply_mpstat(metrics: InstanceMetrics, mpstat_data: dict[str, Any]) -> None:
        """Apply mpstat-derived CPU usage to the metrics object."""

        overall = mpstat_data.get("overall")
        if isinstance(overall, float | int):
            metrics.cpu_usage_pct = float(overall)
        per_core = mpstat_data.get("per_core")
        if isinstance(per_core, list) and per_core:
            metrics.cpu_usage_pct_per_core = [float(v) for v in per_core]
            if metrics.cpu_count is None:
                metrics.cpu_count = len(per_core)

    @staticmethod
    async def _terminate_subprocess(proc: asyncio.subprocess.Process, label: str) -> None:
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
        except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
            logger.debug("Timed out waiting for %s process to exit: %s", label, exc)

    @staticmethod
    async def check_all_instances(instances: list[Instance]) -> dict[str, InstanceMetrics]:
        """
        Check health of multiple instances concurrently.

        Args:
            instances: List of instances to check

        Returns:
            Dictionary mapping instance ID to InstanceMetrics
        """
        if not instances:
            return {}

        logger.debug(f"Checking health of {len(instances)} instances")

        # Create health check tasks
        tasks = []
        for instance in instances:
            task = asyncio.create_task(HealthChecker.check_instance_health(instance), name=f"health-{instance.name}")
            tasks.append((instance.name, task))

        # Wait for all tasks to complete
        results = {}
        completed_tasks = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)

        for (instance_id, _task), result in zip(tasks, completed_tasks, strict=False):
            if isinstance(result, Exception):
                logger.error(f"Health check failed for {instance_id}: {result}")
                results[instance_id] = InstanceMetrics(reachable=False)
            elif isinstance(result, InstanceMetrics):
                results[instance_id] = result

        reachable_count = sum(1 for metrics in results.values() if metrics.reachable)
        logger.debug(f"Health check completed: {reachable_count}/{len(instances)} instances reachable")

        return results
