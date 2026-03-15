"""Worker capability parsing helpers for OpenBench."""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool
from shogiarena._core.shared.kernel.serialization import json_serialize


def _derive_worker_capabilities(
    build_info: Mapping[str, JsonValue],
    *,
    target_engines: set[str] | None = None,
) -> tuple[dict[str, tuple[str, str]], dict[str, bool]]:
    target_set = {name for name in (target_engines or set()) if isinstance(name, str) and name}
    compilers: dict[str, tuple[str, str]] = {}
    tokens: dict[str, bool] = {}

    for engine_name_raw, engine_info_raw in build_info.items():
        if not isinstance(engine_name_raw, str):
            continue
        engine_name = engine_name_raw.strip()
        if not engine_name:
            continue
        if not isinstance(engine_info_raw, dict):
            continue
        engine_info = {str(key): json_serialize(value) for key, value in engine_info_raw.items()}
        is_private = coerce_bool(engine_info.get("private"))
        if is_private:
            tokens[engine_name] = True
            continue

        candidate = _select_available_compiler(engine_info.get("compilers"))
        compilers[engine_name] = (candidate or "shogiarena", "0")

    for engine_name in target_set:
        info_raw = build_info.get(engine_name)
        if not isinstance(info_raw, dict):
            continue
        info = {str(key): json_serialize(value) for key, value in info_raw.items()}
        if coerce_bool(info.get("private")):
            tokens[engine_name] = True
        else:
            compilers.setdefault(engine_name, ("shogiarena", "0"))

    return compilers, tokens


def _select_available_compiler(raw_candidates: JsonValue | None) -> str | None:
    if not isinstance(raw_candidates, list):
        return None
    for raw in raw_candidates:
        if not isinstance(raw, str):
            continue
        candidate = raw.strip()
        if not candidate:
            continue
        executable = candidate.split(">=", 1)[0].strip()
        if executable and shutil.which(executable):
            return executable
    return None


def _extract_error_message(body: str) -> str | None:
    # OpenBench stores errors in <div class="error-message"><pre>...</pre></div>.
    match = re.search(
        r'<div class="error-message">\s*<pre>(?P<msg>.*?)</pre>\s*</div>',
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    message = re.sub(r"\s+", " ", match.group("msg").strip())
    if not message:
        return None
    return message


__all__ = ["_derive_worker_capabilities", "_extract_error_message"]
