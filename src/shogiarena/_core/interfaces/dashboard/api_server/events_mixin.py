"""Dashboard event wiring and broadcast delegates for Arena API server."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.application.broadcast import ENGINE_IO_LOG_LIMIT, BroadcastHandler
from shogiarena._core.contexts.dashboard.application.event_bus import Event, EventBus
from shogiarena._core.contexts.dashboard.application.events import DashboardEvent, DashboardEventType
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.interfaces.dashboard.spsa.api import SpsaAPI
from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.snapshots import EngineIoTailEntry

logger = logging.getLogger(__name__)


def _sanitize_engine_io_line(line: str) -> str:
    return line.strip()


class ArenaApiServerEventsMixin:
    _event_bus: EventBus[DashboardEvent]
    _broadcast: BroadcastHandler
    _state: DashboardState
    ws_hub: LiveWebSocketHub
    spsa_api: SpsaAPI

    def _register_dashboard_event_handlers(self) -> None:
        self._event_bus.subscribe(DashboardEventType.WORKER_UPDATE, self._handle_worker_update)
        self._event_bus.subscribe(DashboardEventType.ENGINE_IO, self._handle_engine_io)
        self._event_bus.subscribe(DashboardEventType.CLEAR_ENGINE_LOGS, self._handle_clear_engine_logs)
        self._event_bus.subscribe(DashboardEventType.SUMMARY_UPDATE, self._handle_summary_update)
        self._event_bus.subscribe(DashboardEventType.GAMES_SNAPSHOT, self._handle_games_snapshot)
        self._event_bus.subscribe(
            DashboardEventType.ASSIGNMENT_STREAM_PUBLISH,
            self._handle_assignment_stream_publish,
        )
        self._event_bus.subscribe(DashboardEventType.SET_WORKER_SNAPSHOT, self._handle_set_worker_snapshot)
        self._event_bus.subscribe(DashboardEventType.ASSIGN_WORKER_SNAPSHOT, self._handle_assign_worker_snapshot)
        self._event_bus.subscribe(DashboardEventType.UPDATE_ENGINE_OPTIONS, self._handle_update_engine_options)

    def _publish_ws(self, topic: str, payload: JsonObject, *, worker_idx: int | None = None) -> None:
        if self.ws_hub is None:
            return
        self.ws_hub.publish(topic, payload, worker_idx=worker_idx)

    def _publish_assignment_via_event_bus(
        self,
        topic: str,
        payload: JsonObject,
        *,
        worker_idx: int | None = None,
    ) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.ASSIGNMENT_STREAM_PUBLISH,
                worker_idx=worker_idx,
                payload={
                    "topic": topic,
                    "payload": payload,
                },
            )
        )

    def _ws_bootstrap_messages(self, worker_filter: set[int] | None) -> list[tuple[str, JsonObject]]:
        return self._broadcast.build_ws_bootstrap_messages(worker_filter=worker_filter)

    def _resolve_ws_snapshot(self, topic: str) -> list[tuple[str, JsonObject]]:
        return self._broadcast.resolve_ws_snapshot(topic)

    def _ensure_engine_io_buffers(self, gid: str) -> dict[str, deque[EngineIoTailEntry]]:
        return self._state.ensure_engine_io_buffers(gid, ENGINE_IO_LOG_LIMIT)

    def _store_engine_io_entry(
        self,
        *,
        gid: str,
        role: str,
        direction: str,
        line: str,
        ts: int,
        state: str | None = None,
    ) -> EngineIoTailEntry:
        logs = self._ensure_engine_io_buffers(gid)
        sanitized = _sanitize_engine_io_line(line)
        entry: EngineIoTailEntry = {"dir": direction, "line": sanitized, "ts": ts}
        if state:
            entry["state"] = state
        logs[role].append(entry)
        return entry

    def _emit_dashboard_event(self, event: DashboardEvent) -> None:
        self._event_bus.publish(Event(event.event_type, event))

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.WORKER_UPDATE,
                worker_idx=worker_idx,
                payload=to_json_object(payload),
            )
        )

    def _resolve_engine_io_target(self, payload_dict: Mapping[str, object]) -> tuple[str, str] | None:
        gid_raw = payload_dict.get("game_id") or payload_dict.get("gid")
        if not gid_raw:
            return None
        gid = str(gid_raw).strip()
        if not gid:
            return None
        role_raw = payload_dict.get("role")
        if not isinstance(role_raw, str) or role_raw not in {"black", "white"}:
            return None
        return gid, role_raw

    def _store_engine_io_raw_entry(self, *, gid: str, role: str, raw: Mapping[str, object]) -> EngineIoTailEntry | None:
        direction_raw = raw.get("direction")
        if not isinstance(direction_raw, str) or direction_raw not in {"in", "out"}:
            return None
        line = raw.get("line")
        if not isinstance(line, str) or not line.strip():
            return None
        ts_raw = raw.get("ts")
        ts = int(ts_raw) if isinstance(ts_raw, int | float) else int(time.time() * 1000)
        state_raw = raw.get("state")
        state = state_raw.strip() if isinstance(state_raw, str) and state_raw.strip() else None
        return self._store_engine_io_entry(
            gid=gid,
            role=role,
            direction=direction_raw,
            line=line,
            ts=ts,
            state=state,
        )

    def _emit_engine_io_entries(self, worker_idx: int, gid: str, role: str, entries: list[EngineIoTailEntry]) -> None:
        if not entries:
            return
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.ENGINE_IO,
                worker_idx=worker_idx,
                payload=to_json_object(
                    {
                        "gid": gid,
                        "role": role,
                        "entries": entries,
                    }
                ),
            )
        )

    def broadcast_engine_io(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None:
        payload_dict: JsonObject = {str(key): value for key, value in payload.items()}
        target = self._resolve_engine_io_target(payload_dict)
        if target is None:
            return
        gid, role = target
        entry = self._store_engine_io_raw_entry(gid=gid, role=role, raw=payload_dict)
        if entry is None:
            return
        self._emit_engine_io_entries(worker_idx, gid, role, [entry])

    def broadcast_engine_io_batch(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None:
        payload_dict: JsonObject = {str(key): value for key, value in payload.items()}
        target = self._resolve_engine_io_target(payload_dict)
        if target is None:
            return
        gid, role = target
        raw_entries = payload_dict.get("entries")
        if not isinstance(raw_entries, list):
            return
        entries: list[EngineIoTailEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            entry = self._store_engine_io_raw_entry(gid=gid, role=role, raw=raw)
            if entry is not None:
                entries.append(entry)
        self._emit_engine_io_entries(worker_idx, gid, role, entries)

    def has_engine_io_subscribers(self, gid: str) -> bool:
        """当該 game の raw engine-I/O topic 購読者がいるかを返す（raw I/O listener の動的 gating 用）。"""
        if self.ws_hub is None:
            return False
        return self.ws_hub.has_engine_io_subscribers(gid)

    def clear_engine_logs(self, game_id: str | int) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.CLEAR_ENGINE_LOGS,
                payload={"game_id": game_id},
            )
        )

    def broadcast_summary_update(self, payload: Mapping[str, JsonValue], *, source: str = "tournament") -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.SUMMARY_UPDATE,
                source=source,
                payload=to_json_object(payload),
            )
        )

    def broadcast_games_snapshot(self, snapshot: Mapping[str, JsonValue], *, event_type: str = "bulk") -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.GAMES_SNAPSHOT,
                payload=to_json_object(snapshot),
                event_type_hint=event_type,
            )
        )

    def set_worker_snapshot(
        self, worker_idx: int, snapshot: Mapping[str, JsonValue], *, should_broadcast: bool = True
    ) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.SET_WORKER_SNAPSHOT,
                worker_idx=worker_idx,
                payload=to_json_object(snapshot),
                should_broadcast=should_broadcast,
            )
        )

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, JsonValue]) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.ASSIGN_WORKER_SNAPSHOT,
                worker_idx=worker_idx,
                payload=to_json_object(snapshot),
            )
        )

    def update_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, JsonValue],
        info: Mapping[str, str] | None = None,
    ) -> None:
        self._emit_dashboard_event(
            DashboardEvent(
                event_type=DashboardEventType.UPDATE_ENGINE_OPTIONS,
                payload=to_json_object(
                    {
                        "engine_name": engine_name,
                        "options": options,
                        "info": info if info else {},
                    }
                ),
            )
        )

    def _handle_worker_update(self, event: Event[DashboardEvent]) -> None:
        if event.payload.worker_idx is None:
            return
        self._broadcast.worker_update(
            event.payload.worker_idx,
            to_json_object(event.payload.payload),
        )

    def _handle_engine_io(self, event: Event[DashboardEvent]) -> None:
        event_payload = event.payload.payload
        if not isinstance(event_payload, Mapping):
            return
        gid = coerce_str(event_payload.get("gid"))
        role = coerce_str(event_payload.get("role"))
        entries = event_payload.get("entries")
        if not gid or role not in {"black", "white"}:
            return
        if not isinstance(entries, list):
            return
        topic = f"live.engine.{gid}.{role}.io.diff"
        if self.ws_hub is not None and self.ws_hub.has_subscribers(topic):
            self.ws_hub.publish(
                topic, {"gid": gid, "role": role, "entries": entries}, worker_idx=event.payload.worker_idx
            )

    def _handle_clear_engine_logs(self, event: Event[DashboardEvent]) -> None:
        raw_gid = event.payload.payload.get("game_id")
        gid = coerce_str(raw_gid)
        if not gid:
            return
        self._state.clear_engine_io_logs(gid.strip())

    def _handle_summary_update(self, event: Event[DashboardEvent]) -> None:
        source = event.payload.source or "tournament"
        self._broadcast.summary_update(to_json_object(event.payload.payload), source=source)

    def _handle_games_snapshot(self, event: Event[DashboardEvent]) -> None:
        self._broadcast.games_snapshot(
            to_json_object(event.payload.payload),
            event_type=event.payload.event_type_hint or "bulk",
        )

    def _handle_assignment_stream_publish(self, event: Event[DashboardEvent]) -> None:
        envelope = event.payload.payload
        topic = coerce_str(envelope.get("topic"))
        payload_raw = envelope.get("payload")
        if not topic or not isinstance(payload_raw, Mapping):
            return
        payload_obj = {str(key): value for key, value in payload_raw.items()}
        self._publish_ws(topic, to_json_object(payload_obj), worker_idx=event.payload.worker_idx)

    def _handle_set_worker_snapshot(self, event: Event[DashboardEvent]) -> None:
        if event.payload.worker_idx is None:
            return
        self._broadcast.set_worker(
            event.payload.worker_idx,
            to_json_object(event.payload.payload),
            should_broadcast=event.payload.should_broadcast,
        )

    def _handle_assign_worker_snapshot(self, event: Event[DashboardEvent]) -> None:
        if event.payload.worker_idx is None:
            return
        self._broadcast.assign_worker_snapshot(
            event.payload.worker_idx,
            to_json_object(event.payload.payload),
        )

    def _handle_update_engine_options(self, event: Event[DashboardEvent]) -> None:
        raw_name = event.payload.payload.get("engine_name")
        engine_name = coerce_str(raw_name)
        if not engine_name:
            return
        raw_options = event.payload.payload.get("options")
        if not isinstance(raw_options, Mapping):
            return
        opts_map = {str(key): value for key, value in raw_options.items()}
        opts_serialized = to_json_object(opts_map)
        info_source = event.payload.payload.get("info")
        info = info_source if isinstance(info_source, Mapping) else {}
        self._state.set_engine_option_snapshot(engine_name, opts_serialized)
        if isinstance(info, Mapping):
            self._state.set_engine_info_entry(
                str(engine_name),
                {str(key): str(value) for key, value in info.items()},
            )


__all__ = ["ArenaApiServerEventsMixin"]
