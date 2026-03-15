"""Instance pool management for allocating and tracking instances."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from pathlib import Path

import yaml

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

from .instance_config_parsing import parse_instances_config
from .instance_models import (
    Instance,
    InstanceActiveGame,
    InstanceActiveGameSide,
    InstanceConfig,
    InstanceType,
)
from .pool_resources import (
    ResourceRequest,
    acquire_resources_with_rollback,
    release_instance_resources,
    validate_resource_requirements,
)

logger = logging.getLogger(__name__)


class InstancePool:
    """
    Manages a pool of instances for job allocation.

    Thread-safe operations for allocating and releasing instances.
    """

    # Mutable class variable; set from the adapter/interface layer at bootstrap.
    DEFAULT_LOCAL_INSTANCES_PATH: Path = Path("instances") / "local.yaml"

    def __init__(self) -> None:
        """Initialize empty instance pool."""
        self._instances: dict[str, Instance] = {}
        self._lock = threading.RLock()

    @classmethod
    def configure_default_local_instances_path(cls, output_dir: Path) -> None:
        """Set the process-wide fallback path for ``load_default_local()``."""

        cls.DEFAULT_LOCAL_INSTANCES_PATH = Path(output_dir) / "instances" / "local.yaml"

    @classmethod
    def load_from_yaml(cls, yaml_path: Path) -> InstancePool:
        """
        Load instance pool from YAML configuration file.

        Args:
            yaml_path: Path to instances YAML file

        Returns:
            Configured InstancePool

        Raises:
            FileNotFoundError: If YAML file doesn't exist
            ValueError: If configuration is invalid
        """
        if not yaml_path.exists():
            raise FileNotFoundError(f"Instances config file not found: {yaml_path}")

        logger.debug("Loading instances from: %s", yaml_path)

        try:
            with open(yaml_path, encoding="utf-8") as f:
                raw_data: JsonValue = json_serialize(yaml.safe_load(f) or {})
        except yaml.YAMLError as exc:
            raise ValueError(f"Failed to parse instances config: {exc}") from exc

        config_data = cls._normalize_config_data(raw_data, yaml_path)
        instances_config = parse_instances_config(config_data)

        pool = cls()
        for instance_config in instances_config.instances:
            instance = Instance(config=instance_config, source_path=yaml_path)
            pool._instances[instance.name] = instance
            logger.debug("Added instance: %s (%s)", instance.name, instance.type.value)

        logger.debug("Loaded %d instances", len(pool._instances))
        return pool

    @staticmethod
    def _normalize_config_data(raw_data: JsonValue | Mapping[str, JsonValue], source_path: Path) -> JsonObject:
        """Expand shorthand YAML forms into the canonical ``{"instances": [...]}`` mapping."""
        if not isinstance(raw_data, dict):
            raise ValueError("Instances config must be a mapping")
        source: JsonObject = {str(key): json_serialize(value) for key, value in raw_data.items()}
        if "instances" in source:
            raise ValueError("'instances:' list format is not supported. Use single-file or hosts style.")

        hosts = source.get("hosts")
        if hosts is not None:
            if not isinstance(hosts, list) or not hosts:
                raise ValueError("'hosts' must be a non-empty list when provided")
            base_name = str(source.get("name") or source_path.stem).strip() or source_path.stem
            expanded: list[JsonObject] = []
            for index, host in enumerate(hosts, start=1):
                entry: JsonObject = {key: value for key, value in source.items() if key != "hosts"}
                entry["host"] = host
                entry["name"] = f"{base_name}-{index:03d}"
                expanded.append(entry)
            return {"instances": expanded}

        entry: JsonObject = dict(source)
        if not str(entry.get("name", "")).strip():
            entry["name"] = source_path.stem
        return {"instances": [entry]}

    @classmethod
    def load_default_local(cls) -> InstancePool | None:
        """Load the default local instances file when present.

        Returns ``None`` when the bundled local instances configuration is absent.
        """

        path = cls.DEFAULT_LOCAL_INSTANCES_PATH
        if not path.exists():
            logger.debug("Default local instances file not found: %s", path)
            return None

        logger.debug("Loading default local instances from %s", path)
        return cls.load_from_yaml(path)

    def _local_instance_locked(self) -> Instance | None:
        for instance in self._instances.values():
            if instance.is_local:
                return instance
        return None

    def get_instance(self, name: str) -> Instance | None:
        """
        Get instance by name.

        Args:
            name: Instance name to retrieve

        Returns:
            Instance if found, None otherwise
        """
        with self._lock:
            return self._instances.get(name)

    def list_instances(self) -> list[Instance]:
        """
        Get list of all instances.

        Returns:
            List of all instances
        """
        with self._lock:
            return list(self._instances.values())

    def ensure_local_instance(self) -> Instance:
        """
        Ensure a local instance exists, creating default if needed.

        Returns:
            Local instance

        Note:
            This method creates the in-memory ``local`` instance on demand and
            does not generate or persist a YAML file on disk.
        """
        with self._lock:
            local_instance = self._local_instance_locked()
            if local_instance:
                return local_instance

            # Create default local instance
            logger.debug("Creating default local instance")
            local_config = InstanceConfig(
                name="local",
                type=InstanceType.LOCAL,
                engine_dir="",
                slots=4,  # Default local slots
            )
            local_instance = Instance(config=local_config, source_path=self.DEFAULT_LOCAL_INSTANCES_PATH)
            self._instances["local"] = local_instance
            return local_instance

    def add_instance(self, config: InstanceConfig, *, source_path: Path | None = None) -> Instance:
        """Add a new instance to the pool."""

        with self._lock:
            if config.name in self._instances:
                raise ValueError(f"Instance already exists: {config.name}")
            instance = Instance(config=config, source_path=source_path)
            self._instances[config.name] = instance
            logger.debug("Added instance to pool: %s", config.name)
            return instance

    def update_instance_config(
        self,
        name: str,
        new_config: InstanceConfig,
        *,
        source_path: Path | None = None,
    ) -> Instance:
        """Replace configuration for an existing instance."""

        with self._lock:
            instance = self._instances.get(name)
            if instance is None:
                raise KeyError(name)
            instance.config = new_config
            if source_path is not None:
                instance.source_path = source_path
            logger.debug("Updated instance config: %s", name)
            return instance

    def remove_instance(self, name: str) -> Instance | None:
        """Remove an instance from the pool."""

        with self._lock:
            instance = self._instances.pop(name, None)
            if instance:
                logger.debug("Removed instance from pool: %s", name)
            return instance

    def try_acquire_resources(self, requirements: Mapping[str, ResourceRequest]) -> bool:
        """
        Attempt to acquire resources on multiple instances atomically.

        Args:
            requirements: Mapping of instance_id -> ResourceRequest to reserve.

        Returns:
            True when all requested resources are acquired, False otherwise.
        """
        if not requirements:
            return True

        with self._lock:
            if not validate_resource_requirements(self._instances, requirements, log=logger):
                return False
            return acquire_resources_with_rollback(self._instances, requirements, log=logger)

    def release_resources(self, allocations: Mapping[str, ResourceRequest]) -> None:
        """Release previously acquired resources for the given instances."""
        if not allocations:
            return

        with self._lock:
            release_instance_resources(self._instances, allocations, log=logger)

    def set_drain(self, name: str, is_drain_enabled: bool) -> Instance | None:
        """
        Set drain status for an instance.

        Args:
            name: Name of instance to modify
            is_drain_enabled: Whether to drain the instance

        Returns:
            Updated instance when found, otherwise None
        """
        with self._lock:
            instance = self._instances.get(name)
            if instance:
                instance.is_draining = is_drain_enabled
                logger.debug("Set drain=%s for instance %s", is_drain_enabled, name)
                return instance
            return None

    def record_active_game(
        self,
        instance_id: str,
        *,
        game_id: str,
        black_engine: str,
        white_engine: str,
        initial_sfen: str,
        role: InstanceActiveGameSide,
        round_index: int | None = None,
        time_control_black: str | None = None,
        time_control_white: str | None = None,
    ) -> None:
        """Record that an instance is currently running a game for dashboard visibility.

        Args:
            instance_id: Target instance name
            game_id: Unique game identifier
            black_engine: Display name for the black-side engine
            white_engine: Display name for the white-side engine
            initial_sfen: Initial position SFEN
            role: Role information specific to this instance
            round_index: Optional tournament round index (0-based)
            time_control_black: Optional stringified time control for black
            time_control_white: Optional stringified time control for white
        """

        with self._lock:
            instance = self._instances.get(instance_id)
            if instance is None:
                logger.debug("Cannot record active game %s, unknown instance %s", game_id, instance_id)
                return

            active = instance.active_game_by_id.get(game_id)
            if active is None:
                active = InstanceActiveGame(
                    game_id=game_id,
                    black_engine=black_engine,
                    white_engine=white_engine,
                    initial_sfen=initial_sfen,
                    time_control_black=time_control_black,
                    time_control_white=time_control_white,
                    round_index=round_index,
                )
                instance.active_game_by_id[game_id] = active
            else:
                if round_index is not None:
                    active.round_index = round_index
                if time_control_black is not None:
                    active.time_control_black = time_control_black
                if time_control_white is not None:
                    active.time_control_white = time_control_white

            active.add_role(role)

    def clear_active_game(self, instance_id: str, game_id: str) -> None:
        """Remove an active game entry when the game finishes."""

        with self._lock:
            instance = self._instances.get(instance_id)
            if instance is None:
                return
            if game_id in instance.active_game_by_id:
                instance.active_game_by_id.pop(game_id, None)

    def get_stats(self) -> dict[str, int]:
        """
        Get pool statistics.

        Returns:
            Dictionary with pool statistics
        """
        with self._lock:
            total = len(self._instances)
            reachable = sum(1 for i in self._instances.values() if i.metrics.is_reachable)
            draining = sum(1 for i in self._instances.values() if i.is_draining)
            in_use_slots = sum(i.metrics.in_use_slots for i in self._instances.values())
            unknown_slots_instances = sum(1 for i in self._instances.values() if not i.is_slot_capacity_known)
            total_slots = sum(i.effective_slots for i in self._instances.values() if i.effective_slots is not None)
            available_slots = sum(
                max(0, i.effective_slots - i.metrics.in_use_slots)
                for i in self._instances.values()
                if i.effective_slots is not None
            )
            active_game_count = sum(len(i.active_game_by_id) for i in self._instances.values())
            active_instances = sum(1 for i in self._instances.values() if i.active_game_by_id)

            return {
                "total_instances": total,
                "reachable_instances": reachable,
                "draining_instances": draining,
                "in_use_slots": in_use_slots,
                "total_slots": total_slots,
                "available_slots": max(0, available_slots),
                "unknown_slots_instances": unknown_slots_instances,
                "active_games": active_game_count,
                "active_instances": active_instances,
            }
