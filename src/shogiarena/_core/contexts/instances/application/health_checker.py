"""Health checking and metrics collection for instances.

SSH transport uses asyncssh (mandatory). There is no system ssh/scp fallback.
Host key verification behavior follows instance configuration (strict by default).
"""

import asyncio
import logging
import platform

from shogiarena._core.contexts.instances.application.ssh_transport import create_transport

from .health_checker_metrics import (
    apply_mpstat,
    collect_local_mpstat,
    collect_remote_mpstat,
    parse_system_output,
    terminate_subprocess,
    windows_fill_cpu_metrics,
    windows_fill_memory_metrics,
    windows_fill_metrics_psutil,
)
from .instance_models import Instance, InstanceMetrics

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
        if instance.is_ssh:
            return await HealthChecker._check_ssh_health(instance)
        logger.warning("Unknown instance type for %s: %s", instance.name, instance.type)
        return InstanceMetrics(is_reachable=False)

    @staticmethod
    async def _check_local_health(instance: Instance) -> InstanceMetrics:
        """Check health of local instance using system commands."""
        sysname = platform.system().lower()
        process: asyncio.subprocess.Process | None = None
        try:
            if sysname == "linux":
                cmd = "uptime; nproc; free -m; top -bn1 | head -5; cat /proc/cpuinfo | grep -m 1 -i 'model name'"
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
                if process.returncode != 0:
                    logger.error(
                        "Local health check failed with return code %s: %s",
                        process.returncode,
                        stderr.decode(),
                    )
                    return InstanceMetrics(is_reachable=False)
                output = stdout.decode("utf-8", errors="ignore")
                metrics = parse_system_output(output)
                mpstat_data = await collect_local_mpstat()
                if mpstat_data:
                    apply_mpstat(metrics, mpstat_data)
                return metrics

            if sysname == "windows":
                metrics = InstanceMetrics(is_reachable=True)
                try:
                    import os

                    metrics.cpu_count = os.cpu_count() or None
                except (OSError, AttributeError, ValueError) as exc:
                    logger.debug("Failed to read cpu_count for %s: %s", instance.name, exc)
                    metrics.cpu_count = None
                is_filled = windows_fill_metrics_psutil(metrics)
                if not is_filled:
                    await windows_fill_memory_metrics(metrics)
                    await windows_fill_cpu_metrics(metrics)
                return metrics

            logger.error("Local health check unsupported on this OS: %s", platform.system())
            return InstanceMetrics(is_reachable=False)

        except TimeoutError:
            if process is not None and process.returncode is None:
                await terminate_subprocess(process, "local health check")
            logger.error("Local health check timed out for %s", instance.name)
            return InstanceMetrics(is_reachable=False)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("Local health check error for %s: %s", instance.name, exc)
            return InstanceMetrics(is_reachable=False)

    @staticmethod
    async def _check_ssh_health(instance: Instance) -> InstanceMetrics:
        """Check health of SSH instance using asyncssh transport."""
        config = instance.config

        if not config.host or not config.user:
            logger.error("SSH instance %s missing host or user configuration", instance.name)
            return InstanceMetrics(is_reachable=False)

        transport = create_transport(instance)
        try:
            await transport.connect()
            rc_os, out_os, err_os = await transport.run("uname -s", timeout=15.0)
            if rc_os != 0:
                logger.error("SSH remote OS check failed for %s: %s", config.host, err_os.strip())
                return InstanceMetrics(is_reachable=False)
            remote_os = out_os.strip()
            if remote_os.lower() != "linux":
                logger.error("Remote health check unsupported on this OS: %s", remote_os)
                return InstanceMetrics(is_reachable=False)

            cmd = "uptime; nproc; free -m; top -bn1 | head -5; cat /proc/cpuinfo | grep -m 1 -i 'model name'"
            rc, out, err = await transport.run(cmd, timeout=30.0)
            if rc != 0:
                logger.error("SSH health check failed for %s: %s", config.host, err)
                return InstanceMetrics(is_reachable=False)
            metrics = parse_system_output(out)
            mpstat_data = await collect_remote_mpstat(transport)
            if mpstat_data:
                apply_mpstat(metrics, mpstat_data)
            return metrics
        except TimeoutError:
            logger.error("SSH health check timed out for %s", config.host)
            return InstanceMetrics(is_reachable=False)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("SSH health check error for %s: %s", config.host, exc)
            return InstanceMetrics(is_reachable=False)
        finally:
            try:
                await transport.close()
            except (OSError, RuntimeError) as exc:
                logger.debug("Failed to close SSH transport for %s: %s", instance.name, exc)
