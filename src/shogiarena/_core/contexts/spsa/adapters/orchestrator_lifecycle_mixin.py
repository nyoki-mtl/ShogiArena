"""Lifecycle and setup helpers for the SPSA orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    build_engine_config_map,
    build_usi_options,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.spsa.adapters.runtime.persistence import append_event, record_ltc_result
from shogiarena._core.contexts.spsa.adapters.runtime.tokens import make_game_token, phase_symbol, variant_token
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, PhaseLiteral
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize


class SpsaOrchestratorLifecycleMixin:
    config: SpsaRunConfig
    run_dir: Path
    _session_uuid: str
    _ltc_results_path: Path | None
    _gid_seq: int
    _update_items: list[int]
    _params: list[ParamEntry]
    _sfens: list[str]
    num_workers: int
    engine_configs: dict[str, EngineConfig]
    baseline_config: Path
    tuned_config: Path
    extra_options: JsonObject | None

    run_items_concurrently: Any
    _run_one_spsa_update: Any
    _store_ltc_baseline: Any

    def _append_spsa_event(self, payload: JsonObject) -> None:
        append_event(self.run_dir, self._session_uuid, payload)

    def _record_ltc_result(self, record: JsonObject) -> None:
        record_ltc_result(
            run_dir=self.run_dir,
            session_uuid=self._session_uuid,
            results_path=self._ltc_results_path,
            record=record,
        )

    def _reserve_pending_game(
        self,
        *,
        update_idx: int,
        phase: PhaseLiteral,
        is_tuned_as_black: bool,
        worker_idx: int,
        event_family: str = "spsa",
    ) -> str:
        """Allocate a game id upfront and emit a pending schedule event."""
        vtoken = variant_token(update_idx)
        game_id = self._make_game_id(vtoken, phase)
        tuned_label = self._tuned_player_label(vtoken, phase)
        baseline_label = self._baseline_player_label(vtoken)
        variant_label = vtoken if phase == "ltc" else vtoken + phase_symbol(phase)
        payload = {
            "event": "game_scheduled",
            "update_idx": int(update_idx),
            "game_id": game_id,
            "variant_token": vtoken,
            "variant_label": variant_label,
            "phase": phase,
            "tuned_as_black": is_tuned_as_black,
            "black_player": tuned_label if is_tuned_as_black else baseline_label,
            "white_player": baseline_label if is_tuned_as_black else tuned_label,
            "status": "pending",
            "assigned_instance": None,
            "worker_idx": worker_idx,
            "start_time": None,
            "family": event_family,
            "is_ltc": event_family == "ltc",
        }
        self._append_spsa_event(payload)
        return game_id

    def _make_game_id(self, variant_token: str, phase: PhaseLiteral) -> str:
        self._gid_seq = (self._gid_seq + 1) % 10_000_000
        token = make_game_token(self._gid_seq)
        if phase == "ltc":
            return f"{variant_token}-ltc-{token}"
        return f"{variant_token}-{token}"

    def _tuned_player_label(
        self,
        variant_token: str,
        phase: PhaseLiteral,
    ) -> str:
        if phase == "ltc":
            return f"{variant_token}-tuned"
        return f"{variant_token}-{phase}"

    @staticmethod
    def _baseline_player_label(variant_token: str) -> str:
        return f"{variant_token}-base"

    async def run(self) -> None:
        # Validate that runner injected update items and parameters
        if not self._update_items:
            raise RuntimeError("SpsaOrchestrator requires update items via set_work_items() before run()")
        if not self._params:
            raise RuntimeError("SpsaOrchestrator requires parameters via set_work_items() before run()")
        if not self._sfens:
            raise RuntimeError("SpsaOrchestrator requires SFENs via set_work_items() before run()")

        # Run update items concurrently using the shared scheduler
        await self.run_items_concurrently(
            self._update_items, self._run_one_spsa_update, concurrency_limit=self.num_workers
        )

    def _prepare_engine_configs(self) -> dict[str, EngineConfig]:
        """Write engine YAMLs and build name-to-spec map for baseline/tuned.

        Returns a dict mapping engine name to the original EngineConfig objects.
        Also stores baseline_config/tuned_config paths as attributes for later
        use and attaches a dynamic 'engine_config' attribute to each spec for
        interface parity with TournamentOrchestrator.
        """
        out_dir = self.run_dir / "inputs" / "engine_configs"
        out_dir.mkdir(parents=True, exist_ok=True)
        base_spec = self.config.baseline[0]
        tuned_spec = self.config.tuned[0]
        if base_spec.engine_path is not None and tuned_spec.engine_path is not None:
            self.baseline_config = Path(base_spec.engine_path)
            self.tuned_config = Path(tuned_spec.engine_path)
            return build_engine_config_map([base_spec, tuned_spec])

        # Merge overlay (max-move sync) + overlays/options like local path does
        base_opts = build_usi_options(self.extra_options, base_spec)
        tuned_opts = build_usi_options(self.extra_options, tuned_spec)

        # Write minimal YAMLs using artifact form when available to defer resolution
        def _write(spec: EngineConfig, filename: str, opts: JsonObject | None) -> Path:
            y: JsonObject = {"name": spec.name}
            art = spec.artifact
            if art and art.strip():
                y["artifact"] = art
                if spec.build_options:
                    y["build_options"] = {str(key): json_serialize(value) for key, value in spec.build_options.items()}
            else:
                # Extract engine_path from the referenced engine_path YAML
                if spec.engine_path is None:
                    raise ValueError("SPSA engine requires either artifact or engine_path")
                raw = yaml.safe_load(Path(spec.engine_path).read_text(encoding="utf-8")) or {}
                ep = raw.get("engine_path")
                if not isinstance(ep, str) or not ep.strip():
                    raise ValueError(f"engine_path missing in engine config: {spec.engine_path}")
                y["engine_path"] = str(ep)
            if spec.mate_default_ply_limit is not None and spec.mate_default_ply_limit > 0:
                y["mate_default_ply_limit"] = spec.mate_default_ply_limit
            if spec.mate_default_node_limit is not None and spec.mate_default_node_limit > 0:
                y["mate_default_node_limit"] = spec.mate_default_node_limit
            y["mate_default_infinite"] = spec.is_mate_default_infinite
            y["mate_wait_for_bestmove"] = spec.should_mate_wait_for_bestmove
            if spec.isready_sync_strategy:
                y["isready_sync_strategy"] = spec.isready_sync_strategy
            if spec.isready_lock_key and spec.isready_lock_key.strip():
                y["isready_lock_key"] = spec.isready_lock_key.strip()
            if spec.isready_lock_template and spec.isready_lock_template.strip():
                y["isready_lock_template"] = spec.isready_lock_template.strip()
            if spec.isready_lock_check_key and spec.isready_lock_check_key.strip():
                y["isready_lock_check_key"] = spec.isready_lock_check_key.strip()
            if spec.isready_lock_check_template and spec.isready_lock_check_template.strip():
                y["isready_lock_check_template"] = spec.isready_lock_check_template.strip()
            if spec.isready_lock_check_templates:
                y["isready_lock_check_templates"] = [str(item) for item in spec.isready_lock_check_templates]
            y["isready_lock_skip_if_exists"] = spec.should_skip_isready_lock_if_exists
            if spec.handshake_timeout is not None:
                y["handshake_timeout"] = float(spec.handshake_timeout)
            if opts:
                y["options"] = opts
            p = out_dir / filename
            p.write_text(yaml.safe_dump(y, sort_keys=False), encoding="utf-8")
            return p

        self.baseline_config = _write(base_spec, "engine_baseline.yaml", base_opts)
        self.tuned_config = _write(tuned_spec, "engine_tuned.yaml", tuned_opts)

        base_spec.engine_path = self.baseline_config
        tuned_spec.engine_path = self.tuned_config

        entries: list[EngineConfig] = [base_spec, tuned_spec]
        return build_engine_config_map(entries)

    def set_work_items(self, items: list[int], params: list[ParamEntry], sfens: list[str]) -> None:
        """Set update items for SPSA tuning.

        Note: This method modifies the `is_not_used` flag of ParamEntry objects in the params list.
        The passed params list elements will have their `is_not_used` flag set to False.
        If you need to preserve the original state, pass a copy of the list.

        Args:
            items: List of update item indices
            params: List of parameter entries (will be modified: is_not_used=False)
            sfens: List of starting positions in SFEN format
        """
        if not items:
            raise ValueError("update items must be non-empty")
        if not params:
            raise ValueError("params must be non-empty")
        if not sfens:
            raise ValueError("sfens must be non-empty")
        # normalize flags for params - WARNING: modifies input list elements
        active_params: list[ParamEntry] = []
        for entry in params:
            if entry.is_not_used:
                continue
            entry.is_not_used = False
            active_params.append(entry)
        self._update_items = list(items)
        self._params = active_params
        self._store_ltc_baseline(active_params, -1)
        self._sfens = list(sfens)
