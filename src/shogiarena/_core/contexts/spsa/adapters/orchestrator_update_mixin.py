"""Parameter update and LTC post-processing helpers for SPSA orchestrator."""

from __future__ import annotations

import hashlib
import random
from typing import Any

from shogiarena._core.contexts.spsa.application.param_io import quantize_value
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.json_types import JsonObject

from .orchestrator_update_flow import run_one_spsa_update


class SpsaOrchestratorUpdateMixin:
    config: Any
    run_dir: Any
    num_workers: int
    _stop_event: Any
    _params: list[ParamEntry]
    _sfens: list[str]
    _params_lock: Any
    _ltc_config: Any
    _ltc_last_completed: int | None
    _ltc_baseline_snapshot: list[ParamEntry] | None
    _ltc_baseline_update_idx: int | None
    _update_batch_execution_service: Any
    _update_delta_service: Any
    _update_recording_service: Any
    _ltc_post_update_service: Any

    _run_game_pair: Any
    _append_spsa_event: Any
    _reserve_pending_game: Any

    def _compute_variant_offsets(
        self,
        reference_params: list[ParamEntry],
        variant_params: list[ParamEntry],
    ) -> dict[str, float]:
        """Compute quantized parameter offsets for a specific SPSA variant."""
        offsets: dict[str, float] = {}
        should_snap_float = bool(self.config.is_snap_float_to_step)
        reference_lookup: dict[str, ParamEntry] = {p.name: p for p in reference_params if not p.is_not_used}
        for variant_entry in variant_params:
            if variant_entry.is_not_used:
                continue
            reference_entry = reference_lookup.get(variant_entry.name)
            if reference_entry is None:
                continue
            reference_value = quantize_value(
                reference_entry,
                reference_entry.value,
                should_snap_float=should_snap_float,
            )
            variant_value = quantize_value(
                variant_entry,
                variant_entry.value,
                should_snap_float=should_snap_float,
            )
            offsets[variant_entry.name] = float(variant_value - reference_value)
        return offsets

    @staticmethod
    def _clone_param_entries(entries: list[ParamEntry]) -> list[ParamEntry]:
        return [
            ParamEntry(
                entry.name,
                entry.type,
                entry.value,
                entry.min,
                entry.max,
                entry.step,
                entry.delta,
                entry.comment,
                entry.is_not_used,
            )
            for entry in entries
        ]

    def _store_ltc_baseline(self, params: list[ParamEntry], update_idx: int) -> None:
        self._ltc_baseline_snapshot = self._clone_param_entries(params)
        self._ltc_baseline_update_idx = update_idx

    def _make_rng(self, idx: int) -> random.Random:
        seed_src = f"{self.config.parameters_path}|{idx}"
        hashed = int(hashlib.sha1(seed_src.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(hashed)

    def _stochastic_round(self, value: float, rng: random.Random) -> int:
        """Apply stochastic rounding for integers to reduce bias."""
        if self.config.int_rounding == "stochastic":
            floor_val = int(value)
            frac = value - floor_val
            return floor_val + (1 if rng.random() < frac else 0)
        else:
            return int(round(value))

    def _build_engine_option_map(
        self,
        params: list[ParamEntry],
        *,
        rng: random.Random | None = None,
        should_allow_stochastic: bool = False,
    ) -> JsonObject:
        options: JsonObject = {}
        for entry in params:
            if entry.is_not_used:
                continue
            qv = quantize_value(entry, entry.value, should_snap_float=self.config.is_snap_float_to_step)
            if entry.type == "int":
                if should_allow_stochastic and rng is not None:
                    options[entry.name] = self._stochastic_round(qv, rng)
                else:
                    options[entry.name] = int(round(qv))
            else:
                options[entry.name] = qv
        return options

    def _ltc_should_run(self, update_idx: int) -> bool:
        config = self._ltc_config
        if config is None:
            return False
        if update_idx % config.every_n_updates != 0:
            return False
        if self._ltc_last_completed == update_idx:
            return False
        return True

    async def _run_one_spsa_update(self, update_idx: int) -> None:
        await run_one_spsa_update(self, update_idx)
