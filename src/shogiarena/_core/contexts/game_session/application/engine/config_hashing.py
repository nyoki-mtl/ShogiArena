from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import TypeAlias

from pydantic import BaseModel

from shogiarena._core.shared.kernel.hash_normalization import normalize_for_hash

CpuAffinityToken: TypeAlias = str | int | float | bool


def parse_cpu_affinity_spec(value: str | int | Iterable[CpuAffinityToken] | None) -> tuple[int, ...]:
    """Parse cpu_affinity specification into a normalized tuple of CPU ids."""

    def _ensure_int(token: str | int | float | bool) -> int:
        try:
            parsed = int(token)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cpu_affinity entries must be integers or ranges; got '{token}'") from exc
        if parsed < 0:
            raise ValueError(f"cpu_affinity entries must be >= 0; got {parsed}")
        return parsed

    def _expand_range(token: str) -> Iterable[int]:
        parts = token.split("-", 1)
        if len(parts) != 2:
            return (_ensure_int(token),)
        start = _ensure_int(parts[0])
        end = _ensure_int(parts[1])
        if end < start:
            raise ValueError(f"cpu_affinity range must be ascending; got '{token}'")
        return range(start, end + 1)

    match value:
        case None:
            return ()
        case str() as s:
            tokens = [t.strip() for t in s.split(",") if t.strip()]
            if not tokens:
                return ()
            expanded: list[int] = []
            for token in tokens:
                expanded.extend(_expand_range(token))
        case Iterable() as items:
            expanded = []
            for item in items:
                match item:
                    case str() as s:
                        expanded.extend(_expand_range(s.strip()))
                    case int() | float() | bool():
                        expanded.append(_ensure_int(item))
                    case _:
                        raise ValueError(f"cpu_affinity entries must be integers or ranges; got '{item}'")
        case _:
            raise TypeError(
                f"cpu_affinity must be specified as a string or iterable of ints; got {type(value).__name__}"
            )
    seen: set[int] = set()
    normalized: list[int] = []
    for cpu_id in expanded:
        if cpu_id not in seen:
            normalized.append(cpu_id)
            seen.add(cpu_id)
    return tuple(normalized)


def hash_engine_config(spec: BaseModel, *, length: int = 8) -> str:
    spec_dict = spec.model_dump()
    payload = {
        "engine_path": spec_dict.get("engine_path"),
        "artifact": spec_dict.get("artifact"),
        "build_options": spec_dict.get("build_options"),
        "options": spec_dict.get("options"),
        "options_overlays": spec_dict.get("options_overlays"),
        "mate_default_ply_limit": spec_dict.get("mate_default_ply_limit"),
        "mate_default_node_limit": spec_dict.get("mate_default_node_limit"),
        "mate_default_infinite": spec_dict.get("mate_default_infinite"),
        "mate_wait_for_bestmove": spec_dict.get("mate_wait_for_bestmove"),
        "isready_sync_strategy": spec_dict.get("isready_sync_strategy"),
        "time_control": spec_dict.get("time_control"),
        "instance_id": spec_dict.get("instance_id"),
        "cpu_affinity": spec_dict.get("cpu_affinity"),
        "name_style": spec_dict.get("name_style"),
    }
    normalized = normalize_for_hash(payload)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
