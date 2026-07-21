"""Remote game execution helper for the SPSA orchestrator."""

from __future__ import annotations

import logging
from typing import Any

import rsshogi.record

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import (
    prepare_remote_game_spec as _prepare_remote_game_spec_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_options import (
    build_remote_pair_option_context,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_pair_execution import (
    execute_remote_pair_game as _execute_remote_pair_game_service,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import max_plies_from_rules
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

logger = logging.getLogger(__name__)


class SpsaOrchestratorRemoteGameMixin:
    config: Any
    engine_configs: dict[str, Any]
    baseline_config: Any
    tuned_config: Any
    extra_options: JsonObject | None
    _engine_factory_service: Any

    async def _run_remote_game(
        self,
        *,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        is_tuned_as_black: bool,
        game_id: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        remote_instance: Instance,
        tuned_label: str,
        baseline_label: str,
        tuned_options: JsonObject,
        baseline_options: JsonObject,
    ) -> rsshogi.record.Record:
        """Execute a single SPSA game by delegating both engines to one remote instance."""
        # Keep parameters in signature for parity with local flow.
        _ = tuned_params, current_params
        black_display = tuned_label if is_tuned_as_black else baseline_label
        white_display = baseline_label if is_tuned_as_black else tuned_label
        black_name = black_display
        white_name = white_display

        option_context = build_remote_pair_option_context(
            engine_configs=self.engine_configs,
            extra_options=self.extra_options,
            black_engine_name=str(self.config.baseline[0].name or "baseline"),
            white_engine_name=str(self.config.tuned[0].name or "tuned"),
        )
        base_opts: JsonObject = dict(option_context.black_options)
        tuned_opts: JsonObject = dict(option_context.white_options)
        base_opts.update(baseline_options)
        tuned_opts.update(tuned_options)

        b_cfg = self.tuned_config if is_tuned_as_black else self.baseline_config
        w_cfg = self.baseline_config if is_tuned_as_black else self.tuned_config

        max_plies = max_plies_from_rules(self.config.rules)
        executor, remote_root, spec = await _prepare_remote_game_spec_service(
            self,
            remote_instance=remote_instance,
            black_config_path=b_cfg,
            white_config_path=w_cfg,
            black_options=(tuned_opts if is_tuned_as_black else base_opts),
            white_options=(base_opts if is_tuned_as_black else tuned_opts),
            start_sfen=start_sfen,
            game_id=game_id,
            black_name=black_name,
            white_name=white_name,
            black_limits=black_limits,
            white_limits=white_limits,
            max_plies=max_plies,
            engine_factory_service=self._engine_factory_service,
        )

        logger.info("[%s] start game %s: %s vs %s", remote_instance.name, game_id, black_display, white_display)
        remote_result = await _execute_remote_pair_game_service(
            self,
            executor=executor,
            remote_root=remote_root,
            prepared_spec=spec,
            game_id=game_id,
            start_sfen=start_sfen,
            black_name=black_display,
            white_name=white_display,
            black_limits=black_limits,
            white_limits=white_limits,
        )
        game_info = remote_result.game_info
        logger.info(
            "[%s] end game %s: result=%s",
            remote_instance.name,
            game_id,
            game_info.result.value,
        )
        game_info.update_metadata(
            {
                "game_type": "spsa",
                "black_player": black_display,
                "white_player": white_display,
            }
        )
        return game_info
