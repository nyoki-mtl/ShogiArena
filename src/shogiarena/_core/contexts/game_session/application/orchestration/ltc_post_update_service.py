"""SPSA post-update LTC handling service."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class SpsaParamEntryPort(Protocol):
    """Minimal parameter entry contract used by post-update service."""

    name: str
    value: float
    is_not_used: bool


ParamEntryT = TypeVar("ParamEntryT", bound=SpsaParamEntryPort)


RunLtcRegressionPort = Callable[..., Awaitable[JsonObject]]
AppendSpsaEventPort = Callable[[JsonObject], None]
PersistRevertIndexPort = Callable[[dict[str, float], int, JsonObject], None]
CommitLtcDecisionPort = Callable[
    [JsonObject, bool, dict[str, float], dict[str, float], int],
    None,
]


@dataclass(frozen=True)
class SpsaLtcPostUpdateRequest(Generic[ParamEntryT]):
    """Input DTO for SPSA post-update LTC handling."""

    update_idx: int
    should_run_ltc_after_update: bool
    pre_update_snapshot: list[ParamEntryT] | None
    post_update_snapshot: list[ParamEntryT] | None
    baseline_snapshot: list[ParamEntryT] | None
    baseline_update_idx: int | None


class SpsaLtcPostUpdateService(Generic[ParamEntryT]):
    """Handle LTC regression decision and optional parameter revert."""

    async def process(
        self,
        *,
        request: SpsaLtcPostUpdateRequest[ParamEntryT],
        orchestrator: object,
        params: list[ParamEntryT],
        params_lock: asyncio.Lock,
        stop_event: asyncio.Event,
        run_ltc_regression: RunLtcRegressionPort,
        clone_param_entries: Callable[[list[ParamEntryT]], list[ParamEntryT]],
        store_ltc_baseline: Callable[[list[ParamEntryT], int], None],
        append_spsa_event: AppendSpsaEventPort,
        write_params: Callable[[list[ParamEntryT]], None],
        persist_revert_index: PersistRevertIndexPort,
        commit_ltc_decision: CommitLtcDecisionPort,
    ) -> None:
        baseline_params_for_ltc = request.baseline_snapshot
        baseline_idx_for_ltc = request.baseline_update_idx
        if baseline_params_for_ltc is None and request.pre_update_snapshot is not None:
            baseline_params_for_ltc = request.pre_update_snapshot
            baseline_idx_for_ltc = (request.update_idx - 1) if request.update_idx > 0 else -1

        if not request.should_run_ltc_after_update:
            return

        if request.post_update_snapshot is None or baseline_params_for_ltc is None:
            logger.warning(
                "Skipping LTC regression for update %s: insufficient parameter snapshots (post=%s, baseline=%s)",
                request.update_idx,
                request.post_update_snapshot is not None,
                baseline_params_for_ltc is not None,
            )
            return

        if stop_event.is_set():
            return

        effective_baseline_idx = baseline_idx_for_ltc if baseline_idx_for_ltc is not None else -1
        ltc_record = await run_ltc_regression(
            orchestrator,
            update_idx=request.update_idx,
            tuned_params=request.post_update_snapshot,
            baseline_params=baseline_params_for_ltc,
            baseline_update_idx=effective_baseline_idx,
        )
        if not ltc_record:
            return

        status = ltc_record.get("status")
        accepted_params_map = _active_params_map(request.post_update_snapshot)
        reverted_params_map = _active_params_map(baseline_params_for_ltc)
        if status == "passed":
            commit_ltc_decision(
                ltc_record,
                True,
                accepted_params_map,
                reverted_params_map,
                effective_baseline_idx,
            )
            async with params_lock:
                store_ltc_baseline(request.post_update_snapshot, request.update_idx)
            return

        if status != "failed":
            return
        commit_ltc_decision(
            ltc_record,
            False,
            accepted_params_map,
            reverted_params_map,
            effective_baseline_idx,
        )

        async with params_lock:
            baseline_by_name = {entry.name: entry for entry in baseline_params_for_ltc}
            if set(baseline_by_name) != {entry.name for entry in params}:
                raise ValueError("LTC baseline parameter names do not match active parameters")
            # ParamEntry objects are shared with SpsaRunnerState. Mutate them in place so the
            # orchestrator, final result, and current.json keep one authoritative theta after a
            # rejected LTC update. Replacing the list left the runner pointing at stale values.
            for entry in params:
                entry.value = float(baseline_by_name[entry.name].value)
            write_params(params)
            revert_params_map = _active_params_map(params)

        revert_timestamp = int(time.time() * 1000)
        logger.info(
            "Reverting update %s parameters to baseline update %s after LTC rejection",
            request.update_idx,
            effective_baseline_idx,
        )
        normalized_revert_idx = max(0, int(effective_baseline_idx))
        append_spsa_event(
            {
                "event": "update",
                "update_idx": int(request.update_idx),
                "params": revert_params_map,
                "timestamp": revert_timestamp,
                "ltc_rejected": True,
                "is_ltc_rejected": True,
                "ltc_reverted_to": normalized_revert_idx,
            }
        )
        persist_revert_index(
            revert_params_map,
            revert_timestamp,
            {
                "ltc_rejected": True,
                "is_ltc_rejected": True,
                "ltc_reverted_to": normalized_revert_idx,
            },
        )


def _active_params_map(params: list[ParamEntryT]) -> dict[str, float]:
    return {entry.name: entry.value for entry in params if not entry.is_not_used}


__all__ = ["SpsaLtcPostUpdateRequest", "SpsaLtcPostUpdateService"]
