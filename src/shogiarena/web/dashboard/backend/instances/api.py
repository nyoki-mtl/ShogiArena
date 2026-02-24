"""Instance management API handlers for Arena Dashboard."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import cast

from aiohttp import web
from aiohttp.web_exceptions import HTTPBadRequest

from shogiarena.arena.instances.health import HealthChecker
from shogiarena.arena.instances.models import Instance, InstanceConfig, InstanceType
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.instances.provision import Provisioner, ProvisionError
from shogiarena.arena.instances.store import InstanceConfigStore
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.persistence.records import InstanceSnapshot
from shogiarena.db.factory import SQLiteShogiDBFactory
from shogiarena.utils.common import project_dirs
from shogiarena.utils.types.coerce import coerce_int_strict, coerce_str
from shogiarena.utils.types.types import JsonObject
from shogiarena.web.dashboard.backend.http_helpers import json_error_response

logger = logging.getLogger(__name__)


class InstancesAPIHandler:
    """API handlers for instance management endpoints."""

    def __init__(
        self,
        instance_pool: InstancePool | None = None,
        *,
        store: InstanceConfigStore | None = None,
        db_path: Path | None = None,
    ):
        """
        Initialize instance API handler.

        Args:
            instance_pool: Instance pool for management (can be None if no instances configured)
        """
        self.instance_pool = instance_pool
        self.store = store or InstanceConfigStore()
        self._db_path = Path(db_path).resolve() if db_path else None
        self._instances_sse_seq = 0
        self._last_health_checks: dict[str, float] = {}
        self._health_check_min_interval = self._load_health_check_min_interval()
        self._health_check_lock = asyncio.Lock()
        self._instances_updated = asyncio.Event()
        self._update_queue: asyncio.Queue[JsonObject] = asyncio.Queue()

    @staticmethod
    def _load_health_check_min_interval() -> float:
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_HEALTH_CHECK_MIN_INTERVAL", "").strip()
        if not raw:
            return 5.0
        try:
            parsed = float(raw)
        except ValueError:
            return 5.0
        return max(0.0, parsed)

    def _get_pool(self) -> InstancePool:
        """Get an instance pool, ensuring a local instance exists.

        If no pool was injected, create one and ensure a default local instance.
        """
        if self.instance_pool is None:
            self.instance_pool = InstancePool()
        # ensure_local_instance is not expected to fail; do not over-catch
        self.instance_pool.ensure_local_instance()
        return self.instance_pool

    def _next_instances_seq(self) -> int:
        self._instances_sse_seq += 1
        return self._instances_sse_seq

    def _queue_instances_update(self, kind: str, *, instance_ids: list[str] | None = None) -> None:
        payload = {"kind": kind}
        if instance_ids:
            payload["instance_ids"] = list(instance_ids)
        self._update_queue.put_nowait(payload)
        self._instances_updated.set()

    async def _drain_updates(self) -> list[JsonObject]:
        updates: list[JsonObject] = []
        while True:
            try:
                updates.append(self._update_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return updates

    @staticmethod
    def _build_instances_delta(
        snapshot: JsonObject,
        updates: list[JsonObject],
    ) -> JsonObject | None:
        if not updates:
            return None
        upsert_ids: set[str] = set()
        removed_ids: set[str] = set()
        send_full = False
        for update in updates:
            kind = update.get("kind")
            ids_raw = update.get("instance_ids")
            ids: list[object]
            if isinstance(ids_raw, list):
                ids = cast(list[object], ids_raw)
            else:
                ids = []
            if kind == "full":
                send_full = True
            elif kind == "remove":
                removed_ids.update(str(x) for x in ids if x)
            else:
                upsert_ids.update(str(x) for x in ids if x)

        if send_full:
            return {
                "instances": snapshot.get("instances", []),
                "stats": snapshot.get("stats", {}),
                "timestamp": snapshot.get("timestamp"),
            }

        instances = snapshot.get("instances", [])
        if isinstance(instances, list):
            updated = []
            for entry in instances:
                if not isinstance(entry, dict):
                    continue
                entry_id = dict(entry).get("id")
                if isinstance(entry_id, str) and entry_id in upsert_ids:
                    updated.append(entry)
        else:
            updated = []

        return {
            "instances": updated,
            "removed": sorted(removed_ids),
            "stats": snapshot.get("stats", {}),
            "timestamp": snapshot.get("timestamp"),
        }

    @staticmethod
    def _extract_last_event_id(request: web.Request) -> int | None:
        raw = request.headers.get("Last-Event-ID")
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _inject_resume_from(payload: JsonObject, last_event_id: int | None) -> JsonObject:
        if last_event_id is None:
            return payload
        if "resume_from" in payload:
            return payload
        next_payload = dict(payload)
        next_payload["resume_from"] = last_event_id
        return next_payload

    @staticmethod
    def _format_sse_event(event_type: str, payload: JsonObject, *, event_id: str | None = None) -> bytes:
        parts = []
        if event_id is not None:
            parts.append(f"id: {event_id}")
        parts.append(f"event: {event_type}")
        parts.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
        return ("\n".join(parts) + "\n\n").encode()

    def _build_instances_payload(self) -> JsonObject:
        pool = self._get_pool()
        instances = pool.list_instances()
        instance_data = [instance.to_dict() for instance in instances]
        stats = pool.get_stats()
        return {
            "instances": instance_data,
            "stats": stats,
            "timestamp": time.time(),
        }

    @staticmethod
    def _signature_for_instances_snapshot(snapshot: JsonObject) -> str:
        pruned = dict(snapshot)
        pruned.pop("timestamp", None)
        instances = pruned.get("instances")
        if isinstance(instances, list):
            normalized_instances = []
            for entry in instances:
                if not isinstance(entry, dict):
                    normalized_instances.append(entry)
                    continue
                inst_copy = dict(entry)
                metrics = inst_copy.get("metrics")
                if isinstance(metrics, dict):
                    metrics_copy = dict(metrics)
                    metrics_copy.pop("timestamp", None)
                    inst_copy["metrics"] = metrics_copy
                normalized_instances.append(inst_copy)
            pruned["instances"] = normalized_instances
        return json.dumps(pruned, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _normalize_tags(raw: str | list[str] | None) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            stripped = raw.strip()
            return [stripped] if stripped else []
        if not isinstance(raw, list):
            raise ValueError("tags must be a list of strings")
        tags: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise ValueError("tags must contain only strings")
            tag = item.strip()
            if tag:
                tags.append(tag)
        return tags

    def _build_config_from_payload(
        self,
        payload: JsonObject,
        *,
        existing: InstanceConfig | None = None,
    ) -> InstanceConfig:
        name = str(payload.get("name") or "").strip() if existing is None else existing.name
        if not name:
            raise ValueError("name is required")
        if existing is not None and "name" in payload and payload["name"] != existing.name:
            raise ValueError("instance name is immutable")

        raw_type = (
            payload.get("type")
            if payload.get("type") is not None
            else (existing.type.value if existing is not None else None)
        )
        if raw_type is None:
            raise ValueError("type is required")
        try:
            inst_type = InstanceType(str(raw_type))
        except ValueError as exc:
            raise ValueError("invalid instance type") from exc

        raw_slots = (
            payload.get("slots")
            if payload.get("slots") is not None
            else (existing.slots if existing is not None else None)
        )
        if raw_slots is None:
            raise ValueError("slots is required")
        try:
            slots = coerce_int_strict(raw_slots, "slots")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if slots < 0:
            raise ValueError("slots must be >= 0 (0 means auto)")

        raw_tags_obj = payload.get("tags", existing.tags if existing is not None else None)
        if raw_tags_obj is not None and not isinstance(raw_tags_obj, str | list):
            raise ValueError("tags must be a list of strings")
        if isinstance(raw_tags_obj, list):
            for item in raw_tags_obj:
                if not isinstance(item, str):
                    raise ValueError("tags must contain only strings")
            raw_tags: str | list[str] | None = cast(list[str], raw_tags_obj)
        else:
            raw_tags = raw_tags_obj
        tags = self._normalize_tags(raw_tags)

        strict_hkc = payload.get("strict_host_key_checking")
        if strict_hkc is None and existing is not None:
            strict_hkc = existing.strict_host_key_checking
        if strict_hkc is None:
            strict_hkc = True
        strict_host_key_checking = bool(strict_hkc)

        host = payload.get("host") if payload.get("host") is not None else (existing.host if existing else None)
        user = payload.get("user") if payload.get("user") is not None else (existing.user if existing else None)
        port_raw = payload.get("port") if payload.get("port") is not None else (existing.port if existing else 22)
        identity_file = (
            payload.get("identity_file")
            if payload.get("identity_file") is not None
            else (existing.identity_file if existing else None)
        )
        project_root = (
            payload.get("project_root")
            if payload.get("project_root") is not None
            else (existing.project_root if existing else "")
        )
        if project_root is not None and not isinstance(project_root, str):
            raise ValueError("project_root must be a string")

        if port_raw is None:
            port_raw = existing.port if existing is not None else 22
        try:
            port = coerce_int_strict(port_raw, "port")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        if inst_type is InstanceType.SSH:
            if not coerce_str(host):
                raise ValueError("SSH instances require 'host'")
            if not coerce_str(user):
                raise ValueError("SSH instances require 'user'")
        else:
            host = None
            user = None
            identity_file = None
            project_root = ""

        raw_max_engines = (
            payload.get("max_engines")
            if payload.get("max_engines") is not None
            else (existing.max_engines if existing is not None else None)
        )
        if raw_max_engines in ("", None):
            max_engines = None
        else:
            try:
                max_engines = coerce_int_strict(raw_max_engines, "max_engines")
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if max_engines <= 0:
                raise ValueError("max_engines must be positive when provided")

        install_requirements_raw = (
            payload.get("install_requirements")
            if payload.get("install_requirements") is not None
            else (existing.install_requirements if existing is not None else False)
        )
        install_requirements = bool(install_requirements_raw)

        engine_dir = ""

        return InstanceConfig(
            name=name,
            type=inst_type,
            engine_dir=engine_dir,
            project_root=str(project_root or ""),
            host=str(host) if host is not None else None,
            user=str(user) if user is not None else None,
            port=port,
            identity_file=str(identity_file) if identity_file else None,
            slots=slots,
            max_engines=max_engines,
            tags=tags,
            strict_host_key_checking=strict_host_key_checking,
            install_requirements=install_requirements,
        )

    @staticmethod
    def _resolve_local_path(raw_path: str) -> Path:
        candidate = Path(raw_path.strip())
        if not candidate.is_absolute():
            candidate = (project_dirs.output_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        root_resolved = project_dirs.output_dir.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("local_path must be inside the output directory") from exc
        return candidate

    @staticmethod
    def _expand_remote_path(template: str, instance: Instance) -> str:
        value = template.replace("{engine_dir}", instance.config.engine_dir or "")
        value = value.replace("{project_root}", instance.config.project_root)
        return value

    async def get_instances(self, request: web.Request) -> web.Response:
        """
        GET /api/instances - List all instances with current status.

        Returns:
            JSON array of instance objects with health/metrics
        """
        return web.json_response(self._build_instances_payload())

    async def sse_instances(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 3.0) or 3.0)
        poll_interval = max(1.0, min(poll_interval, 15.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: JsonObject) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            seq = self._next_instances_seq()
            envelope = dict(payload)
            envelope.setdefault("stream", "instances")
            envelope["seq"] = seq
            chunk = self._format_sse_event("instances", envelope, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                updates = await self._drain_updates()
                snapshot = self._build_instances_payload()
                signature = self._signature_for_instances_snapshot(snapshot)
                if send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "instances_update",
                            "data": snapshot,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    send_initial = False
                elif updates:
                    delta = self._build_instances_delta(snapshot, updates)
                    if delta:
                        await push_event(
                            {
                                "type": "instances_delta",
                                "data": delta,
                                "timestamp": int(time.time() * 1000),
                            }
                        )
                        last_signature = signature

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                try:
                    await asyncio.wait_for(self._instances_updated.wait(), timeout=poll_interval)
                    self._instances_updated.clear()
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("Instances SSE client disconnected")
        except Exception:
            logger.exception("Unexpected error in instances SSE")

        return response

    async def get_instance(self, request: web.Request) -> web.Response:
        """
        GET /api/instances/{id} - Get specific instance details.

        Returns:
            JSON object with instance details
        """
        pool = self._get_pool()
        instance_id = request.match_info["id"]

        instance = pool.get_instance(instance_id)
        if not instance:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        return web.json_response(instance.to_dict())

    async def get_instance_games(self, request: web.Request) -> web.Response:
        """Return recent games for an instance, including completed history."""

        pool = self._get_pool()
        instance_id = request.match_info["id"]
        instance = pool.get_instance(instance_id)
        if instance is None:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        query = request.rel_url.query
        state = str(query.get("state", "active")).lower()
        if state not in {"active", "completed", "all"}:
            return json_error_response(
                "state must be one of active|completed|all",
                status=400,
                code="invalid_state",
            )

        try:
            limit = int(query.get("limit", 20))
        except ValueError:
            return json_error_response("limit must be an integer", status=400, code="invalid_limit")
        if limit <= 0:
            return json_error_response("limit must be positive", status=400, code="invalid_limit")
        limit = min(limit, 200)

        try:
            offset = int(query.get("offset", 0))
        except ValueError:
            return json_error_response("offset must be an integer", status=400, code="invalid_offset")
        if offset < 0:
            return json_error_response("offset must be non-negative", status=400, code="invalid_offset")

        include_active = state in {"active", "all"}
        include_completed = state in {"completed", "all"}

        def _to_iso(value: object | None) -> str | None:
            if value is None:
                return None
            if isinstance(value, int | float):
                return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc).isoformat()
            return str(value)

        games: list[JsonObject] = []

        def _active_sort_key(item: JsonObject) -> tuple[int, str]:
            started = item.get("started_at")
            if isinstance(started, str):
                return (0, started)
            return (1, "")

        if include_active:
            active_entries: list[JsonObject] = []
            for active in instance.active_games.values():
                active_entries.append(
                    {
                        "status": "active",
                        "game_id": active.game_id,
                        "game_name": active.game_id,
                        "black_engine": active.black_engine,
                        "white_engine": active.white_engine,
                        "started_at": _to_iso(active.started_at),
                        "round_index": active.round_index,
                        "time_control": {
                            "black": active.time_control_black,
                            "white": active.time_control_white,
                        },
                        "roles": [role.to_dict() for role in active.roles],
                    }
                )
            active_entries.sort(key=_active_sort_key, reverse=True)
            games.extend(active_entries[:limit])

        remaining_limit = limit
        if include_completed:
            remaining_limit = max(limit - len(games), 0)
            if remaining_limit > 0:
                if self._db_path is None:
                    return json_error_response(
                        "game history unavailable; database path not configured",
                        status=503,
                        code="history_unavailable",
                    )
                try:
                    with ArenaDBService(SQLiteShogiDBFactory(self._db_path)) as db_service:
                        history = db_service.get_instance_game_history(
                            instance_id,
                            limit=remaining_limit,
                            offset=offset,
                        )
                except Exception:
                    logger.exception("Failed to query game history for instance %s", instance_id)
                    return web.json_response({"error": "Failed to query game history"}, status=500)

                for record in history:
                    role_payloads = []
                    roles_raw = record.get("roles", [])
                    if not isinstance(roles_raw, list):
                        roles_raw = []
                    for role_entry in roles_raw:
                        if not isinstance(role_entry, dict):
                            continue
                        role_payloads.append(
                            {
                                "role": role_entry.get("role"),
                                "engine_name": role_entry.get("engine_name"),
                                "engine_display_name": role_entry.get("engine_display_name"),
                                "binary_path": role_entry.get("binary_path"),
                                "build_flags": role_entry.get("build_flags"),
                                "started_at": _to_iso(role_entry.get("started_at")),
                                "completed_at": _to_iso(role_entry.get("completed_at")),
                                "extra": role_entry.get("extra"),
                                "engine_artifact_id": role_entry.get("engine_artifact_id"),
                                "local": True,
                            }
                        )
                    games.append(
                        {
                            "status": "completed",
                            "game_id": record.get("game_name") or str(record.get("game_id")),
                            "game_name": record.get("game_name"),
                            "numeric_game_id": record.get("game_id"),
                            "result_code": record.get("result_code"),
                            "black_engine": record.get("black_engine"),
                            "white_engine": record.get("white_engine"),
                            "started_at": _to_iso(record.get("started_at")),
                            "completed_at": _to_iso(record.get("completed_at")),
                            "run_id": record.get("run_id"),
                            "roles": role_payloads,
                        }
                    )

        games = games[:limit]

        return web.json_response(
            {
                "instance_id": instance_id,
                "state": state,
                "count": len(games),
                "games": games,
            }
        )

    async def create_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (JSONDecodeError, HTTPBadRequest, ValueError):
            return json_error_response("Invalid JSON payload", status=400, code="invalid_json")

        try:
            config = self._build_config_from_payload(payload)
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        if pool.get_instance(config.name) is not None:
            return json_error_response(
                f"Instance '{config.name}' already exists",
                status=409,
                code="instance_exists",
            )

        instance: Instance | None = None
        try:
            instance = pool.add_instance(config)
            path = self.store.save(instance.config)
            instance.source_path = path
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to create instance %s: %s", config.name, exc)
            if instance is not None:
                pool.remove_instance(config.name)
            return json_error_response(
                "Failed to persist instance configuration",
                status=500,
                code="persist_failed",
            )

        self._queue_instances_update("upsert", instance_ids=[instance.name])
        return web.json_response(instance.to_dict(), status=201)

    async def patch_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        instance_id = request.match_info["id"]
        instance = pool.get_instance(instance_id)
        if instance is None:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (JSONDecodeError, HTTPBadRequest, ValueError):
            return json_error_response("Invalid JSON payload", status=400, code="invalid_json")

        try:
            updated_config = self._build_config_from_payload(payload, existing=instance.config)
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        in_use = instance.metrics.in_use_slots
        if updated_config.slots is not None:
            capacity = updated_config.slots
        else:
            cpu_count = instance.metrics.cpu_count
            if isinstance(cpu_count, int) and cpu_count > 0:
                capacity = cpu_count
            else:
                capacity = max(1, in_use)
        if capacity < in_use:
            return json_error_response(
                "slots cannot be decreased below current in-use slots",
                status=409,
                code="slots_in_use",
                in_use_slots=in_use,
            )

        if updated_config.type != instance.type and (in_use or instance.active_games):
            return json_error_response(
                "cannot change instance type while jobs are running",
                status=409,
                code="instance_busy",
            )

        try:
            path = self.store.save(updated_config)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to persist instance update for %s: %s", instance_id, exc)
            return json_error_response(
                "Failed to persist instance configuration",
                status=500,
                code="persist_failed",
            )

        instance = pool.update_instance_config(instance_id, updated_config, source_path=path)
        self._queue_instances_update("upsert", instance_ids=[instance_id])
        return web.json_response(instance.to_dict())

    async def delete_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        instance_id = request.match_info["id"]
        instance = pool.get_instance(instance_id)
        if instance is None:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        if instance.is_local:
            return json_error_response(
                "cannot delete local instance",
                status=400,
                code="local_instance",
            )

        if instance.metrics.in_use_slots > 0 or instance.active_games:
            return json_error_response(
                "cannot delete instance with active jobs",
                status=409,
                code="instance_busy",
                in_use_slots=instance.metrics.in_use_slots,
                active_games=list(instance.active_games.keys()),
            )

        pool.remove_instance(instance_id)
        try:
            self.store.delete(instance_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to delete config file for %s: %s", instance_id, exc)
        self._queue_instances_update("remove", instance_ids=[instance_id])
        return web.Response(status=204)

    async def post_instance_action(self, request: web.Request) -> web.Response:
        """
        POST /api/instances/{id}/actions - Perform action on instance.

        Expected JSON body:
            {"action": "health_check|drain|undrain"}

        Returns:
            JSON result of the action
        """
        pool = self._get_pool()
        instance_id = request.match_info["id"]

        try:
            body = await request.json()
        except (JSONDecodeError, HTTPBadRequest):
            logger.exception("Invalid JSON in request body for instance action")
            return json_error_response(
                "Invalid JSON in request body",
                status=400,
                code="invalid_json",
            )

        action = body.get("action")
        if not action:
            return json_error_response(
                "Missing 'action' field in request body",
                status=400,
                code="missing_action",
            )

        instance = pool.get_instance(instance_id)
        if not instance:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        # Execute the requested action
        if action == "health_check":
            result = await self._perform_health_check(instance)
        elif action == "drain":
            result = self._perform_drain(instance, drain=True)
        elif action == "undrain":
            result = self._perform_drain(instance, drain=False)
        elif action == "provision":
            try:
                result = await self._perform_provision(instance, body)
            except ValueError as exc:
                return json_error_response(str(exc), status=400, code="invalid_payload")
        else:
            return json_error_response(
                f"Unknown action: {action}",
                status=400,
                code="unknown_action",
            )

        if result.get("success"):
            if action == "health_check":
                self._queue_instances_update("upsert", instance_ids=[instance.name])
            else:
                self._queue_instances_update("upsert", instance_ids=[instance.name])
        return web.json_response(result)

    async def get_instance_metrics(self, request: web.Request) -> web.Response:
        """
        GET /api/instances/{id}/metrics - Get current metrics for instance.

        Returns:
            JSON object with instance metrics
        """
        pool = self._get_pool()
        instance_id = request.match_info["id"]

        instance = pool.get_instance(instance_id)
        if not instance:
            return json_error_response(
                f"Instance '{instance_id}' not found",
                status=404,
                code="instance_not_found",
            )

        return web.json_response(
            {
                "instance_id": instance_id,
                "metrics": instance.metrics.model_dump(),
                "timestamp": time.time(),
            }
        )

    async def _perform_health_check(self, instance: Instance, *, force: bool = False) -> JsonObject:
        """Perform health check on instance and update metrics."""
        try:
            logger.debug(f"Performing health check on instance {instance.name}")

            now = time.monotonic()
            last = self._last_health_checks.get(instance.name)
            if not force and last is not None and (now - last) < self._health_check_min_interval:
                result = {
                    "action": "health_check",
                    "instance_id": instance.name,
                    "success": True,
                    "reachable": instance.metrics.reachable,
                    "metrics": instance.metrics.model_dump(),
                    "timestamp": time.time(),
                    "skipped": True,
                }
                return result
            self._last_health_checks[instance.name] = now

            # Perform health check
            new_metrics = await HealthChecker.check_instance_health(instance)

            # Update instance metrics
            instance.update_metrics(new_metrics)
            self._queue_instances_update("upsert", instance_ids=[instance.name])

            if self._db_path is not None and new_metrics.reachable:
                snapshot = InstanceSnapshot(
                    instance_id=instance.name,
                    display_name=instance.name,
                    host_label=instance.config.host,
                    cpu_model=new_metrics.cpu_model,
                    cpu_arch=None,
                    cpu_cores=new_metrics.cpu_count,
                    cpu_threads=new_metrics.cpu_count,
                    memory_total_mb=new_metrics.mem_total_mb,
                    os_info=None,
                    gpu_model=None,
                    gpu_vendor=None,
                    gpu_vram_mb=None,
                    gpu_count=None,
                    instance_type=instance.type.value,
                    tags=tuple(instance.config.tags or []),
                    extra={
                        "slots": instance.config.slots,
                        "reachable": new_metrics.reachable,
                        "engine_dir": instance.config.engine_dir,
                        "project_root": instance.config.project_root,
                        "is_local": instance.is_local,
                        "is_ssh": instance.is_ssh,
                    },
                )
                try:
                    with ArenaDBService(SQLiteShogiDBFactory(self._db_path)) as db_service:
                        db_service.upsert_instance_spec(snapshot)
                except Exception:
                    logger.exception("Failed to persist instance spec for %s", instance.name)

            return {
                "action": "health_check",
                "instance_id": instance.name,
                "success": True,
                "reachable": new_metrics.reachable,
                "metrics": new_metrics.model_dump(),
                "timestamp": time.time(),
            }

        except (asyncio.TimeoutError, OSError, RuntimeError) as e:
            logger.error(f"Health check failed for instance {instance.name}: {e}")
            return {
                "action": "health_check",
                "instance_id": instance.name,
                "success": False,
                "error": str(e),
                "timestamp": time.time(),
            }

    async def run_health_checks(self, *, force: bool = False) -> None:
        """Run health checks for all instances, avoiding overlap."""
        if self._health_check_lock.locked():
            return
        async with self._health_check_lock:
            pool = self._get_pool()
            instances = pool.list_instances()
            if not instances:
                return
            await asyncio.gather(
                *[self._perform_health_check(instance, force=force) for instance in instances],
            )

    def _perform_drain(self, instance: Instance, drain: bool) -> JsonObject:
        """Set drain status on instance."""
        old_drain = instance.drain
        if self.instance_pool is None:
            return {
                "action": "drain" if drain else "undrain",
                "instance_id": instance.name,
                "success": False,
                "error": "Instance pool not available",
                "timestamp": time.time(),
            }
        success = self.instance_pool.set_drain(instance.name, drain)

        if success:
            action_name = "drain" if drain else "undrain"
            logger.info(f"Successfully set {action_name} on instance {instance.name}")
            return {
                "action": action_name,
                "instance_id": instance.name,
                "success": True,
                "old_drain": old_drain,
                "new_drain": drain,
                "timestamp": time.time(),
            }
        else:
            return {
                "action": "drain" if drain else "undrain",
                "instance_id": instance.name,
                "success": False,
                "error": "Failed to update drain status",
                "timestamp": time.time(),
            }

    async def _perform_provision(self, instance: Instance, payload: JsonObject) -> JsonObject:
        if instance.type is not InstanceType.SSH:
            raise ValueError("provision is only supported for SSH instances")

        local_raw = payload.get("local_path")
        remote_raw = payload.get("remote_path")
        mode = str(payload.get("mode") or "dir").strip().lower()
        executable = bool(payload.get("executable"))

        local_str = coerce_str(local_raw)
        if not local_str:
            raise ValueError("'local_path' is required")
        remote_str = coerce_str(remote_raw)
        if not remote_str:
            raise ValueError("'remote_path' is required")

        local_path = self._resolve_local_path(local_str)
        if mode == "dir":
            if not local_path.is_dir():
                raise ValueError("local_path must reference a directory when mode='dir'")
        elif mode == "file":
            if not local_path.is_file():
                raise ValueError("local_path must reference a file when mode='file'")
        else:
            raise ValueError("mode must be either 'dir' or 'file'")

        remote_path = self._expand_remote_path(remote_str, instance).strip()
        if not remote_path:
            raise ValueError("remote_path resolved to an empty value")

        try:
            if mode == "dir":
                await Provisioner.ensure_remote_dir_by_manifest(instance, local_path, remote_path)
            else:
                await Provisioner.ensure_remote_file(instance, local_path, remote_path, executable=executable)
        except ProvisionError as exc:
            logger.error("Provision failed for instance %s: %s", instance.name, exc)
            return {
                "action": "provision",
                "instance_id": instance.name,
                "success": False,
                "error": str(exc),
                "local_path": str(local_path),
                "remote_path": remote_path,
                "mode": mode,
                "timestamp": time.time(),
            }

        logger.info(
            "Provisioned %s (%s) -> %s for instance %s",
            local_path,
            mode,
            remote_path,
            instance.name,
        )

        return {
            "action": "provision",
            "instance_id": instance.name,
            "success": True,
            "local_path": str(local_path),
            "remote_path": remote_path,
            "mode": mode,
            "timestamp": time.time(),
        }

    def setup_routes(self, app: web.Application) -> None:
        """
        Setup instance management routes.

        Args:
            app: aiohttp application to add routes to
        """
        # Instance management endpoints
        app.router.add_post("/api/instances", self.create_instance)
        app.router.add_get("/api/instances", self.get_instances)
        app.router.add_get("/api/instances/stream", self.sse_instances)
        app.router.add_get("/api/instances/{id}", self.get_instance)
        app.router.add_get("/api/instances/{id}/games", self.get_instance_games)
        app.router.add_patch("/api/instances/{id}", self.patch_instance)
        app.router.add_delete("/api/instances/{id}", self.delete_instance)
        app.router.add_post("/api/instances/{id}/actions", self.post_instance_action)
        app.router.add_get("/api/instances/{id}/metrics", self.get_instance_metrics)
