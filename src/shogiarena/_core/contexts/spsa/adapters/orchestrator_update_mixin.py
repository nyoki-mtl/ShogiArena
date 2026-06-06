"""Parameter update and LTC post-processing helpers for SPSA orchestrator."""

from __future__ import annotations

import hashlib
import math
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
                entry.option_name,
                entry.value_encoding,
                entry.scale,
                entry.significant_digits,
            )
            for entry in entries
        ]

    def _store_ltc_baseline(self, params: list[ParamEntry], update_idx: int) -> None:
        self._ltc_baseline_snapshot = self._clone_param_entries(params)
        self._ltc_baseline_update_idx = update_idx

    def _make_rng(self, idx: int) -> random.Random:
        seed_base = getattr(self.config, "space_path", None) or "spsa"
        seed_src = f"{seed_base}|{idx}"
        hashed = int(hashlib.sha1(seed_src.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(hashed)

    def _stochastic_round(self, value: float, rng: random.Random) -> int:
        """Apply stochastic rounding for integers to reduce bias."""
        if self.config.int_rounding == "stochastic":
            # Use math.floor (not int(), which truncates toward zero) so negative
            # values round symmetrically and stay consistent with the pair builder.
            return math.floor(value + rng.random())
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
            should_defer_int_round = (
                entry.type == "int" and should_allow_stochastic and self.config.int_rounding == "stochastic"
            )
            qv = quantize_value(
                entry,
                entry.value,
                should_snap_float=self.config.is_snap_float_to_step,
                should_round_int=not should_defer_int_round,
            )
            option_name = entry.engine_option_name
            if entry.type == "int":
                if should_allow_stochastic and rng is not None:
                    options[option_name] = self._stochastic_round(qv, rng)
                else:
                    options[option_name] = int(round(qv))
            elif entry.value_encoding == "scaled_integer":
                if entry.scale is None or entry.scale <= 0:
                    raise ValueError(f"scaled_integer parameter '{entry.name}' requires positive scale")
                options[option_name] = int(round(qv * entry.scale))
            elif entry.value_encoding == "decimal":
                options[option_name] = _format_canonical_decimal(qv, significant_digits=entry.significant_digits)
            else:
                options[option_name] = qv
        return options

    def _build_engine_option_maps_for_pair(
        self,
        plus_params: list[ParamEntry],
        minus_params: list[ParamEntry],
        *,
        rng: random.Random,
    ) -> tuple[JsonObject, JsonObject]:
        """Build plus/minus option maps with shared stochastic integer samples."""
        plus_options: JsonObject = {}
        minus_options: JsonObject = {}
        minus_by_name = {entry.name: entry for entry in minus_params}
        for plus_entry in plus_params:
            if plus_entry.is_not_used:
                continue
            minus_entry = minus_by_name.get(plus_entry.name)
            if minus_entry is None or minus_entry.is_not_used:
                continue
            should_defer_int_round = plus_entry.type == "int" and self.config.int_rounding == "stochastic"
            plus_qv = quantize_value(
                plus_entry,
                plus_entry.value,
                should_snap_float=self.config.is_snap_float_to_step,
                should_round_int=not should_defer_int_round,
            )
            minus_qv = quantize_value(
                minus_entry,
                minus_entry.value,
                should_snap_float=self.config.is_snap_float_to_step,
                should_round_int=not should_defer_int_round,
            )
            option_name = plus_entry.engine_option_name
            if plus_entry.type == "int":
                if self.config.int_rounding == "stochastic":
                    sample = rng.random()
                    plus_options[option_name] = math.floor(plus_qv + sample)
                    minus_options[option_name] = math.floor(minus_qv + sample)
                else:
                    plus_options[option_name] = int(round(plus_qv))
                    minus_options[option_name] = int(round(minus_qv))
            elif plus_entry.value_encoding == "scaled_integer":
                if plus_entry.scale is None or plus_entry.scale <= 0:
                    raise ValueError(f"scaled_integer parameter '{plus_entry.name}' requires positive scale")
                minus_scale = minus_entry.scale if minus_entry.scale is not None else plus_entry.scale
                if minus_scale <= 0:
                    raise ValueError(f"scaled_integer parameter '{minus_entry.name}' requires positive scale")
                plus_options[option_name] = int(round(plus_qv * plus_entry.scale))
                minus_options[option_name] = int(round(minus_qv * minus_scale))
            elif plus_entry.value_encoding == "decimal":
                plus_options[option_name] = _format_canonical_decimal(
                    plus_qv,
                    significant_digits=plus_entry.significant_digits,
                )
                minus_options[option_name] = _format_canonical_decimal(
                    minus_qv,
                    significant_digits=minus_entry.significant_digits,
                )
            else:
                plus_options[option_name] = plus_qv
                minus_options[option_name] = minus_qv
        return plus_options, minus_options

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


def _format_canonical_decimal(value: float, *, significant_digits: int) -> str:
    if not math.isfinite(value):
        raise ValueError(f"decimal option value must be finite: {value!r}")
    digits = max(1, int(significant_digits))
    text = f"{float(value):.{digits}g}"
    if "e" in text or "E" in text:
        text = f"{float(value):.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", "+0", ""}:
        return "0"
    if text.startswith("+"):
        text = text[1:]
    return text
