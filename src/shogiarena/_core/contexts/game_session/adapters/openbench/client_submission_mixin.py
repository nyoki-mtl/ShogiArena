"""Submission, heartbeat, and recovery mixin for OpenBench client."""

from __future__ import annotations

import platform
import uuid
from collections.abc import Callable
from logging import getLogger
from typing import Protocol, cast

import psutil

from shogiarena._core.platform.host_probe.platform_detectors import get_cpu_info
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int, coerce_str

from .client_http_mixin import OpenBenchClientHttpMixin
from .client_types import OpenBenchClientConfig, OpenBenchCounters, OpenBenchError

logger = getLogger(__name__)

_RECOVERABLE_WORKER_ERRORS = frozenset(
    {
        "Invalid Secret Token",
        "Bad Machine Id",
        "Server Configuration Changed",
    }
)


class _RecoveryPort(Protocol):
    async def _register_worker(self) -> None: ...

    async def _claim_target_workload(self) -> None: ...


class OpenBenchClientSubmissionMixin(OpenBenchClientHttpMixin):
    _config: OpenBenchClientConfig
    _machine_id: int | None
    _secret: str | None
    _workload_test_id: int | None
    _result_id: int | None
    _has_assignment: bool
    _worker_compilers: dict[str, tuple[str, str]]
    _worker_tokens: dict[str, bool]
    _client_version: int

    async def _submit_results(self, delta: OpenBenchCounters, *, persist_state: Callable[[], None]) -> bool:
        return await self._submit_results_once(
            delta,
            should_allow_recovery=True,
            persist_state=persist_state,
        )

    async def _submit_results_once(
        self,
        delta: OpenBenchCounters,
        *,
        should_allow_recovery: bool,
        persist_state: Callable[[], None],
    ) -> bool:
        if self._workload_test_id is None or self._result_id is None:
            raise OpenBenchError("OpenBench workload is not assigned")
        payload: dict[str, str | int] = {
            "machine_id": self._machine_id or 0,
            "secret": self._secret or "",
            "test_id": self._workload_test_id,
            "result_id": self._result_id,
            **delta.to_payload(),
        }
        response = await self._post("clientSubmitResults", payload)
        if "error" in response:
            error = coerce_str(response.get("error")) or "unknown_error"
            if should_allow_recovery and self._is_recoverable_worker_error(error):
                await self._recover_worker_assignment(error=error, endpoint="clientSubmitResults")
                try:
                    persist_state()
                except Exception as exc:
                    raise OpenBenchError(
                        f"Failed to persist recovered OpenBench assignment before result retry: {exc}"
                    ) from exc
                return await self._submit_results_once(
                    delta,
                    should_allow_recovery=False,
                    persist_state=persist_state,
                )
            raise OpenBenchError(f"OpenBench result submission failed: {error}")
        stop = response.get("stop")
        return coerce_bool(stop)

    async def _heartbeat_once(self, *, should_allow_recovery: bool) -> bool:
        if self._workload_test_id is None:
            raise OpenBenchError("OpenBench workload is not assigned")
        payload: dict[str, str | int] = {
            "machine_id": self._machine_id or 0,
            "secret": self._secret or "",
            "test_id": self._workload_test_id,
        }
        response = await self._post("clientHeartbeat", payload)
        if "error" in response:
            error = coerce_str(response.get("error")) or "unknown_error"
            if should_allow_recovery and self._is_recoverable_worker_error(error):
                await self._recover_worker_assignment(error=error, endpoint="clientHeartbeat")
                return await self._heartbeat_once(should_allow_recovery=False)
            raise OpenBenchError(f"OpenBench heartbeat failed: {error}")
        stop = response.get("stop")
        return coerce_bool(stop)

    async def _recover_worker_assignment(self, *, error: str, endpoint: str) -> None:
        recovery_port = cast(_RecoveryPort, self)
        logger.warning(
            "OpenBench %s returned recoverable worker error (%s); re-registering worker and reclaiming workload",
            endpoint,
            error,
        )
        self._machine_id = None
        self._secret = None
        self._workload_test_id = None
        self._result_id = None
        self._has_assignment = False
        await recovery_port._register_worker()
        await recovery_port._claim_target_workload()

    @staticmethod
    def _is_recoverable_worker_error(error: str) -> bool:
        return error.strip() in _RECOVERABLE_WORKER_ERRORS

    def _build_system_info(self) -> JsonObject:
        cpu = get_cpu_info()
        raw_flags = cpu.get("flags", [])
        flags = [x.replace("_", "").replace(".", "").upper() for x in raw_flags if isinstance(x, str)]
        compilers = {name: [spec[0], spec[1]] for name, spec in sorted(self._worker_compilers.items())}
        tokens = {name: coerce_bool(value) for name, value in sorted(self._worker_tokens.items())}
        return {
            "compilers": compilers,
            "tokens": tokens,
            "cpu_flags": sorted(set(flags)),
            "cpu_name": coerce_str(cpu.get("brand_raw")) or "Unknown",
            "os_name": platform.system(),
            "os_ver": platform.release(),
            "python_ver": platform.python_version(),
            "mac_address": hex(uuid.getnode()).upper()[2:],
            "logical_cores": max(1, coerce_int(psutil.cpu_count(logical=True)) or 1),
            "physical_cores": max(
                1,
                coerce_int(psutil.cpu_count(logical=False)) or coerce_int(psutil.cpu_count(logical=True)) or 1,
            ),
            "ram_total_mb": max(0, coerce_int(psutil.virtual_memory().total // (1024**2)) or 0),
            "machine_id": "None",
            "machine_name": "shogiarena",
            "concurrency": max(1, coerce_int(self._config.concurrency) or 1),
            "sockets": 1,
            "syzygy_max": 0,
            "noisy": False,
            "focus": [],
            "cxx_comp": "unknown",
            "fastchess_ver": "0",
            "shogitest_ver": "0",
            "client_ver": coerce_int(self._client_version) or 0,
        }
