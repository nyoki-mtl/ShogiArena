"""Assignment and worker registration mixin for OpenBench client."""

from __future__ import annotations

import asyncio
import json
import re
from logging import getLogger
from typing import Protocol, cast

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize

from .client_http_mixin import OpenBenchClientHttpMixin
from .client_types import OpenBenchClientConfig, OpenBenchError
from .client_worker_capabilities import _derive_worker_capabilities, _extract_error_message

logger = getLogger(__name__)


class _AssignmentBuildInfoPort(Protocol):
    def _build_system_info(self) -> JsonObject: ...


class OpenBenchClientAssignmentMixin(OpenBenchClientHttpMixin):
    _config: OpenBenchClientConfig
    _machine_id: int | None
    _secret: str | None
    _workload_test_id: int | None
    _result_id: int | None
    _blacklist: set[int]
    _has_assignment: bool
    _client_version: int
    _worker_compilers: dict[str, tuple[str, str]]
    _worker_tokens: dict[str, bool]
    _tested_engine: str
    _base_engine: str

    async def _verify_credentials(self) -> None:
        response = await self._post("clientVersionRef", self._auth_payload())
        if "error" in response:
            raise OpenBenchError(f"OpenBench authentication failed: {response['error']}")
        client_version = response.get("client_version")
        if isinstance(client_version, int):
            self._client_version = client_version

    async def _ensure_target_test_id(self) -> None:
        if self._config.mode != "create_test":
            return
        existing = self._config.target_test_id
        if isinstance(existing, int) and existing > 0:
            return
        payload = dict(self._config.create_payload or {})
        if not payload:
            raise OpenBenchError("OpenBench create payload is empty")
        pre_ids = await self._fetch_user_test_ids()
        await self._submit_create_test(payload)
        created_id = await self._discover_created_test_id(
            before_ids=pre_ids,
            payload=payload,
            timeout_sec=self._config.create_discovery_timeout_sec,
        )
        self._config.target_test_id = created_id

    async def _register_worker(self) -> None:
        build_info_port = cast(_AssignmentBuildInfoPort, self)
        payload: dict[str, str] = {
            **self._auth_payload(),
            "system_info": json.dumps(build_info_port._build_system_info(), ensure_ascii=False),
        }
        response = await self._post("clientWorkerInfo", payload)
        if "error" in response:
            raise OpenBenchError(f"OpenBench worker registration failed: {response['error']}")
        machine_id = response.get("machine_id")
        secret = response.get("secret")
        if not isinstance(machine_id, int) or not isinstance(secret, str) or not secret:
            raise OpenBenchError("OpenBench worker registration returned invalid machine credentials")
        self._machine_id = machine_id
        self._secret = secret

    async def _prepare_worker_capabilities(self) -> None:
        try:
            response = await self._get("clientGetBuildInfo")
        except OpenBenchError as exc:
            logger.warning("Failed to fetch OpenBench build info; proceeding with minimal worker capabilities: %s", exc)
            return
        if not isinstance(response, dict):
            logger.warning("OpenBench clientGetBuildInfo returned non-object payload; ignoring")
            return
        compilers, tokens = _derive_worker_capabilities(response, target_engines=self._target_engine_names())
        self._worker_compilers = compilers
        self._worker_tokens = tokens

    def _target_engine_names(self) -> set[str]:
        names = {self._tested_engine, self._base_engine}
        payload = self._config.create_payload or {}
        for key in ("dev_engine", "base_engine"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                names.add(raw.strip())
        return {name for name in names if name}

    async def _claim_target_workload(self) -> None:
        deadline = asyncio.get_running_loop().time() + self._config.assignment_timeout_sec
        while True:
            response = await self._post(
                "clientGetWorkload",
                {
                    "machine_id": self._machine_id,
                    "secret": self._secret,
                    "blacklist": [str(x) for x in sorted(self._blacklist)],
                },
            )
            if "error" in response:
                raise OpenBenchError(f"OpenBench workload request failed: {response['error']}")
            workload = response.get("workload")
            if isinstance(workload, dict):
                wl: JsonObject = {str(key): json_serialize(value) for key, value in workload.items()}
                test_raw = wl.get("test")
                result_raw = wl.get("result")
                test = (
                    {str(key): json_serialize(value) for key, value in test_raw.items()}
                    if isinstance(test_raw, dict)
                    else {}
                )
                result = (
                    {str(key): json_serialize(value) for key, value in result_raw.items()}
                    if isinstance(result_raw, dict)
                    else {}
                )
                test_id = coerce_int(test.get("id"))
                result_id = coerce_int(result.get("id"))
                if test_id == self._config.target_test_id and result_id is not None:
                    self._workload_test_id = self._config.target_test_id
                    self._result_id = result_id
                    self._has_assignment = True
                    return
                if test_id is not None:
                    self._blacklist.add(test_id)
            if asyncio.get_running_loop().time() >= deadline:
                raise OpenBenchError(
                    f"OpenBench target workload assignment timed out (target_test_id={self._config.target_test_id})"
                )
            await asyncio.sleep(self._config.poll_interval_sec)

    async def _submit_create_test(self, payload: dict[str, str]) -> None:
        create_payload = {**payload, **self._auth_payload(), "action": "CREATE_TEST"}
        _status, _final_url, body = await self._post_text("scripts", create_payload, should_allow_redirects=True)
        if "Unable to authenticate user" in body or "Bad Credentials" in body:
            raise OpenBenchError("OpenBench CREATE_TEST authentication failed")
        error_message = _extract_error_message(body)
        if error_message:
            raise OpenBenchError(f"OpenBench CREATE_TEST failed: {error_message}")

    async def _discover_created_test_id(
        self,
        *,
        before_ids: set[int],
        payload: dict[str, str],
        timeout_sec: float,
    ) -> int:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while True:
            ids = await self._fetch_user_test_ids()
            candidates = sorted((i for i in ids if i not in before_ids), reverse=True)
            for test_id in candidates:
                if await self._is_matching_created_test(test_id, payload):
                    return test_id
            if asyncio.get_running_loop().time() >= deadline:
                raise OpenBenchError("Failed to detect created OpenBench test id within timeout")
            await asyncio.sleep(self._config.poll_interval_sec)

    async def _fetch_user_test_ids(self) -> set[int]:
        _status, _url, body = await self._post_text(f"user/{self._config.username}", self._auth_payload())
        ids: set[int] = set()
        for match in re.findall(r"/test/(\d+)/", body):
            test_id = coerce_int(match)
            if test_id is not None:
                ids.add(test_id)
        return ids

    async def _is_matching_created_test(self, test_id: int, payload: dict[str, str]) -> bool:
        _status, _url, body = await self._post_text(f"test/{test_id}", self._auth_payload())
        must_include = [
            payload.get("dev_repo", ""),
            payload.get("base_repo", ""),
            payload.get("dev_engine", ""),
            payload.get("base_engine", ""),
            payload.get("dev_time_control", ""),
            payload.get("base_time_control", ""),
        ]
        for key in ("dev_branch", "base_branch"):
            value = payload.get(key, "").strip()
            if value and not re.fullmatch(r"[0-9a-fA-F]{40}", value):
                must_include.append(value)
        for token in must_include:
            if token and token not in body:
                return False
        return True
