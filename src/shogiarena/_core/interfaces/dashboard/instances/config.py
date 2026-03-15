"""Configuration mapping helpers for instances API payloads."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.instances.application.entrypoints import (
    Instance,
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_int_strict,
    coerce_optional_text,
    coerce_str,
)


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


def build_config_from_payload(
    payload: JsonObject,
    *,
    existing: InstanceConfig | None = None,
) -> InstanceConfig:
    name = coerce_optional_text(payload.get("name")) or "" if existing is None else existing.name
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
    if isinstance(raw_type, InstanceType):
        inst_type = raw_type
    else:
        raw_type_text = coerce_optional_text(raw_type)
        if raw_type_text is None:
            raise ValueError("invalid instance type")
        try:
            inst_type = InstanceType(raw_type_text)
        except ValueError as exc:
            raise ValueError("invalid instance type") from exc

    raw_slots = (
        payload.get("slots") if payload.get("slots") is not None else (existing.slots if existing is not None else None)
    )
    if raw_slots is None:
        raise ValueError("slots is required")
    try:
        slots = coerce_int_strict(raw_slots, "slots")
    except ValueError as exc:
        raise ValueError(exc.args[0]) from exc
    if slots < 0:
        raise ValueError("slots must be >= 0 (0 means auto)")

    raw_tags_obj = payload.get("tags", existing.tags if existing is not None else None)
    if raw_tags_obj is not None and not isinstance(raw_tags_obj, str | list):
        raise ValueError("tags must be a list of strings")
    if isinstance(raw_tags_obj, list):
        for item in raw_tags_obj:
            if not isinstance(item, str):
                raise ValueError("tags must contain only strings")
        raw_tags: str | list[str] | None = [item for item in raw_tags_obj if isinstance(item, str)]
    else:
        raw_tags = raw_tags_obj
    tags = _normalize_tags(raw_tags)

    is_strict_hkc = payload.get("is_strict_host_key_checking")
    if is_strict_hkc is None and existing is not None:
        is_strict_hkc = existing.is_strict_host_key_checking
    if is_strict_hkc is None:
        is_strict_hkc = True
    is_strict_host_key_checking = coerce_bool(is_strict_hkc)

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
        raise ValueError(exc.args[0]) from exc

    if inst_type == InstanceType.SSH:
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
            raise ValueError(exc.args[0]) from exc
        if max_engines <= 0:
            raise ValueError("max_engines must be positive when provided")

    install_requirements_raw = (
        payload.get("should_install_requirements")
        if payload.get("should_install_requirements") is not None
        else (existing.should_install_requirements if existing is not None else False)
    )
    should_install_requirements = coerce_bool(install_requirements_raw)

    engine_dir = ""
    normalized_project_root = coerce_optional_text(project_root) or ""
    normalized_host = coerce_optional_text(host)
    normalized_user = coerce_optional_text(user)
    normalized_identity_file = coerce_optional_text(identity_file)

    return InstanceConfig(
        name=name,
        type=inst_type,
        engine_dir=engine_dir,
        project_root=normalized_project_root,
        host=normalized_host,
        user=normalized_user,
        port=port,
        identity_file=normalized_identity_file,
        slots=slots,
        max_engines=max_engines,
        tags=tags,
        is_strict_host_key_checking=is_strict_host_key_checking,
        should_install_requirements=should_install_requirements,
    )


def resolve_local_path(raw_path: str) -> Path:
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


def expand_remote_path(template: str, instance: Instance) -> str:
    value = template.replace("{engine_dir}", instance.config.engine_dir or "")
    value = value.replace("{project_root}", instance.config.project_root)
    return value


__all__ = [
    "build_config_from_payload",
    "expand_remote_path",
    "resolve_local_path",
]
