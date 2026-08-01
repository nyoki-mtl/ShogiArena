"""Remote game execution helper for the SPSA orchestrator."""

from __future__ import annotations

import logging
from typing import Any

import rsshogi.record

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    build_usi_option_layers,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    resolve_effective_handshake_timeout,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    attach_participation_metadata,
    collect_participation_records_remote_pair,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import (
    prepare_remote_game_spec as _prepare_remote_game_spec_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_lifecycle import (
    manage_remote_pair_instance_lifecycle,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_pair_execution import (
    execute_remote_pair_game as _execute_remote_pair_game_service,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import max_plies_from_rules
from shogiarena._core.contexts.spsa.domain.participation_identity import (
    SpsaParticipationIdentity,
    attach_spsa_participation_identity,
)
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
    session_context: Any
    _timeout_reclassification_enabled: bool
    _default_engine_handshake_timeout: float | None

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
        black_engine_id: str,
        white_engine_id: str,
        tuned_variant_id: str,
        baseline_variant_id: str,
        clear_hash_before_game: bool,
        after_variant_setoption: str,
        black_item: Any,
        white_item: Any,
        update_idx: int,
        pair_id: str,
        observation_kind: str,
    ) -> rsshogi.record.Record:
        """Execute a single SPSA game by delegating both engines to one remote instance."""
        # Keep parameters in signature for parity with local flow.
        _ = tuned_params, current_params
        black_display = tuned_label if is_tuned_as_black else baseline_label
        white_display = baseline_label if is_tuned_as_black else tuned_label
        black_name = black_engine_id
        white_name = white_engine_id

        baseline_layers = build_usi_option_layers(self.extra_options, self.config.baseline[0])
        tuned_layers = build_usi_option_layers(self.extra_options, self.config.tuned[0])

        b_cfg = self.tuned_config if is_tuned_as_black else self.baseline_config
        w_cfg = self.baseline_config if is_tuned_as_black else self.tuned_config
        b_engine_config = self.config.tuned[0] if is_tuned_as_black else self.config.baseline[0]
        w_engine_config = self.config.baseline[0] if is_tuned_as_black else self.config.tuned[0]

        max_plies = max_plies_from_rules(self.config.rules)
        remote_result = None
        spec = None
        async with manage_remote_pair_instance_lifecycle(
            self,
            game_id=game_id,
            initial_sfen=start_sfen,
            round_index=update_idx,
            instance_id=remote_instance.name,
            black_engine_name=black_name,
            white_engine_name=white_name,
            black_pool_key=black_item.pool_key,
            white_pool_key=white_item.pool_key,
            black_item=black_item,
            white_item=white_item,
            black_limits=black_limits,
            white_limits=white_limits,
        ):
            executor, remote_root, spec = await _prepare_remote_game_spec_service(
                self,
                remote_instance=remote_instance,
                black_config_path=b_cfg,
                white_config_path=w_cfg,
                black_option_layers=(tuned_layers if is_tuned_as_black else baseline_layers),
                white_option_layers=(baseline_layers if is_tuned_as_black else tuned_layers),
                black_variant_options=(tuned_options if is_tuned_as_black else baseline_options),
                white_variant_options=(baseline_options if is_tuned_as_black else tuned_options),
                black_path_option_names=tuple(
                    (self.config.tuned[0] if is_tuned_as_black else self.config.baseline[0]).path_options
                ),
                white_path_option_names=tuple(
                    (self.config.baseline[0] if is_tuned_as_black else self.config.tuned[0]).path_options
                ),
                black_go_options=dict(b_engine_config.go_options),
                white_go_options=dict(w_engine_config.go_options),
                black_handshake_timeout_s=resolve_effective_handshake_timeout(
                    b_engine_config.handshake_timeout,
                    self._default_engine_handshake_timeout,
                ),
                white_handshake_timeout_s=resolve_effective_handshake_timeout(
                    w_engine_config.handshake_timeout,
                    self._default_engine_handshake_timeout,
                ),
                black_variant_id=tuned_variant_id if is_tuned_as_black else baseline_variant_id,
                white_variant_id=baseline_variant_id if is_tuned_as_black else tuned_variant_id,
                clear_hash_before_game=clear_hash_before_game,
                after_variant_setoption=after_variant_setoption,
                start_sfen=start_sfen,
                game_id=game_id,
                run_id=self.session_context.run_id,
                black_name=black_name,
                white_name=white_name,
                black_limits=black_limits,
                white_limits=white_limits,
                max_plies=max_plies,
                rules=self.config.rules,
                timeout_reclassification_enabled=self._timeout_reclassification_enabled,
                engine_factory_service=self._engine_factory_service,
                update_idx=update_idx,
                pair_id=pair_id,
            )

            logger.info(
                "[%s] start game %s: %s vs %s",
                remote_instance.name,
                game_id,
                black_display,
                white_display,
            )
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
        if spec is None or remote_result is None:
            raise RuntimeError("SPSA remote execution did not produce a sealed result")
        game_info = remote_result.game_info
        participation = collect_participation_records_remote_pair(
            self,
            spec_payload=spec,
            black_engine_name=black_name,
            white_engine_name=white_name,
            black_spec=b_engine_config,
            white_spec=w_engine_config,
            black_pool_key=black_item.pool_key,
            white_pool_key=white_item.pool_key,
            instance_id=remote_instance.name,
            started_at=remote_result.started_at,
            completed_at=remote_result.completed_at,
            remote_execution={
                "endpoint_identity": remote_result.endpoint_identity,
                "deployment_id": remote_result.deployment_id,
                "job_id": remote_result.job_id,
                "attempt_id": remote_result.attempt_id,
                "execution_digest": remote_result.worker_result.execution_digest,
                "coordinator_transport_started_at": remote_result.started_at.isoformat(),
                "coordinator_transport_completed_at": remote_result.completed_at.isoformat(),
                "spsa_run_id": self.session_context.run_id,
                "spsa_update_idx": update_idx,
                "spsa_pair_id": pair_id,
            },
            worker_provenance=remote_result.worker_result.provenance,
        )
        participation = attach_spsa_participation_identity(
            participation,
            identity=SpsaParticipationIdentity(
                run_id=self.session_context.run_id,
                update_idx=update_idx,
                pair_id=pair_id,
                attempt_id=remote_result.attempt_id,
                observation_kind="LTC" if observation_kind == "LTC" else "SPSA",
            ),
        )
        attach_participation_metadata(
            game_record=game_info,
            participation_records=participation,
        )
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
