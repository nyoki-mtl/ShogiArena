"""Summary runtime-context assembly service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from shogiarena._core.contexts.game_session.application.summary.runtime_context import (
    SummaryRuntimeActionRefs,
    SummaryRuntimeBuildRequest,
    SummaryRuntimeDependencies,
    SummaryRuntimeStateRefs,
    TournamentSummaryRuntimeContext,
)
from shogiarena._core.contexts.game_session.ports.summary_runtime import (
    TournamentSummaryApiServerPort,
    TournamentSummaryRecordWriterPort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


class SummaryRuntimeApiServerSourcePort(Protocol):
    """Minimal API-server source contract required by runtime context assembly."""

    def broadcast_games_snapshot(self, snapshot: Mapping[str, object], *, event_type: str = "bulk") -> None: ...
    def broadcast_summary_update(self, payload: Mapping[str, object], *, source: str) -> None: ...


class SummaryRuntimeRecordWriterSourcePort(Protocol):
    """Minimal record-writer source contract required by runtime context assembly."""

    def get_records_summary(self) -> Mapping[str, int]: ...


class SummaryRuntimeOpenBenchClientPort(Protocol):
    """Minimal OpenBench client contract required by strict-mode wiring."""

    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def is_strict(self) -> bool: ...


@dataclass(slots=True)
class _SummaryApiServerAdapter(TournamentSummaryApiServerPort):
    server: SummaryRuntimeApiServerSourcePort

    def broadcast_games_snapshot(self, schedule_snapshot: JsonObject) -> None:
        self.server.broadcast_games_snapshot(schedule_snapshot)

    def broadcast_summary_update(self, summary: JsonObject, *, source: str) -> None:
        self.server.broadcast_summary_update(summary, source=source)


@dataclass(slots=True)
class _SummaryRecordWriterAdapter(TournamentSummaryRecordWriterPort):
    writer: SummaryRuntimeRecordWriterSourcePort

    def get_records_summary(self) -> JsonObject:
        summary: JsonObject = {}
        for key, value in self.writer.get_records_summary().items():
            summary[str(key)] = value
        return summary


class TournamentSummaryRuntimeContextService:
    """Build summary runtime context DTOs from runner-owned state and services."""

    @staticmethod
    def build_api_server_runtime(
        api_server: SummaryRuntimeApiServerSourcePort | None,
    ) -> TournamentSummaryApiServerPort | None:
        if api_server is None:
            return None
        return _SummaryApiServerAdapter(server=api_server)

    @staticmethod
    def build_record_writer_runtime(
        record_writer: SummaryRuntimeRecordWriterSourcePort | None,
    ) -> TournamentSummaryRecordWriterPort | None:
        if record_writer is None:
            return None
        return _SummaryRecordWriterAdapter(writer=record_writer)

    @staticmethod
    def is_openbench_strict_mode(client: SummaryRuntimeOpenBenchClientPort | None) -> bool:
        return bool(client is not None and client.is_strict)

    def build_runtime_context(
        self,
        *,
        request: SummaryRuntimeBuildRequest,
        state: SummaryRuntimeStateRefs,
        dependencies: SummaryRuntimeDependencies,
        actions: SummaryRuntimeActionRefs,
    ) -> TournamentSummaryRuntimeContext:
        return TournamentSummaryRuntimeContext(
            request=request,
            state=state,
            dependencies=SummaryRuntimeDependencies(
                db_service=dependencies.db_service,
                api_server=self.build_api_server_runtime(
                    cast(SummaryRuntimeApiServerSourcePort | None, dependencies.api_server)
                ),
                record_writer=self.build_record_writer_runtime(
                    cast(SummaryRuntimeRecordWriterSourcePort | None, dependencies.record_writer)
                ),
                sprt_service=dependencies.sprt_service,
                is_openbench_strict_mode=self.is_openbench_strict_mode(dependencies.openbench_client),
                openbench_client=dependencies.openbench_client,
                watchdog=dependencies.watchdog,
                stop_controller=dependencies.stop_controller,
            ),
            actions=actions,
        )


__all__ = [
    "SummaryRuntimeApiServerSourcePort",
    "SummaryRuntimeOpenBenchClientPort",
    "SummaryRuntimeRecordWriterSourcePort",
    "TournamentSummaryRuntimeContextService",
]
