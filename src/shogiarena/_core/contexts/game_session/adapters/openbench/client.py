"""OpenBench client facade."""

from __future__ import annotations

from collections.abc import Mapping

import aiohttp

from shogiarena._core.shared.kernel.boundary_parsers.openbench import parse_openbench_client_state_boundary
from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object_serialized as _coerce_json_object_serialized,
)
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort

from .client_assignment_mixin import OpenBenchClientAssignmentMixin
from .client_submission_mixin import OpenBenchClientSubmissionMixin
from .client_totals import compute_totals
from .client_types import OpenBenchClientConfig, OpenBenchCounters


class OpenBenchClient(OpenBenchClientSubmissionMixin, OpenBenchClientAssignmentMixin):
    def __init__(self, config: OpenBenchClientConfig, *, tested_engine: str, base_engine: str) -> None:
        config.validate()
        self._config = config
        self._tested_engine = tested_engine
        self._base_engine = base_engine
        self._http: aiohttp.ClientSession | None = None
        self._machine_id: int | None = None
        self._secret: str | None = None
        self._workload_test_id: int | None = None
        self._result_id: int | None = None
        self._submitted = OpenBenchCounters()
        self._last_synced_games = 0
        self._blacklist: set[int] = set()
        self._has_assignment = False
        self._client_version = 39
        self._worker_compilers: dict[str, tuple[str, str]] = {}
        self._worker_tokens: dict[str, bool] = {}

    @property
    def is_strict(self) -> bool:
        return self._config.is_strict

    @property
    def is_enabled(self) -> bool:
        return self._config.is_enabled

    async def initialize(self) -> None:
        timeout = aiohttp.ClientTimeout(total=30)
        self._http = aiohttp.ClientSession(timeout=timeout)
        await self._verify_credentials()
        await self._prepare_worker_capabilities()
        await self._ensure_target_test_id()
        await self._register_worker()
        await self._claim_target_workload()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    def snapshot_state(self) -> dict[str, object]:
        return {
            "submitted": self._submitted.to_state(),
            "last_synced_games": self._last_synced_games,
            "target_test_id": self._config.target_test_id,
            "claimed_test_id": self._workload_test_id,
            "result_id": self._result_id,
            "blacklist": sorted(self._blacklist),
        }

    def restore_state(self, state: Mapping[str, JsonValue]) -> None:
        parsed = parse_openbench_client_state_boundary(state, path="openbench.state")
        submitted_state = _coerce_json_object_serialized(
            parsed.get("submitted", {}),
            field_name="openbench.submitted",
        )
        self._submitted = OpenBenchCounters.from_state(submitted_state)
        last_synced_games = coerce_int(parsed.get("last_synced_games", 0))
        self._last_synced_games = max(self._submitted.games, last_synced_games or 0)
        target_test_id = coerce_int(parsed.get("target_test_id"))
        if target_test_id is not None:
            self._config.target_test_id = target_test_id
        raw_blacklist = parsed.get("blacklist", [])
        parsed_blacklist: set[int] = set()
        if isinstance(raw_blacklist, list):
            for raw_value in raw_blacklist:
                value = coerce_int(raw_value)
                if value is not None:
                    parsed_blacklist.add(value)
        self._blacklist = parsed_blacklist

    async def try_sync(self, db: DatabaseServicePort) -> bool:
        return await self._submit_if_delta(db, should_check_interval=True)

    async def try_flush(self, db: DatabaseServicePort) -> bool:
        return await self._submit_if_delta(db, should_check_interval=False)

    async def _submit_if_delta(self, db: DatabaseServicePort, *, should_check_interval: bool) -> bool:
        if not self._has_assignment:
            return False
        totals = compute_totals(db, tested_engine=self._tested_engine, base_engine=self._base_engine)
        if should_check_interval and totals.games - self._last_synced_games < self._config.submit_interval_games:
            return False
        delta = totals.delta_from(self._submitted)
        if delta.is_empty():
            self._last_synced_games = totals.games
            return False
        should_stop = await self._submit_results(delta)
        self._submitted = totals
        self._last_synced_games = totals.games
        return should_stop

    async def should_stop_after_heartbeat(self) -> bool:
        if not self._has_assignment:
            return False
        return await self._heartbeat_once(should_allow_recovery=True)


__all__ = [
    "OpenBenchClient",
]
