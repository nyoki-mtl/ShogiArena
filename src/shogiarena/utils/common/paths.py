from __future__ import annotations

import os
import re
from pathlib import Path

from shogiarena.utils.common import project_dirs

_POSIX_ENV_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
_WIN_ENV_PATTERN = re.compile(r"%([^%]+)%")


def expand_env_vars(s: str) -> str:
    """Expand environment variables in a string without using os.path.

    Supports POSIX styles: $VAR and ${VAR}, and Windows style: %VAR%.
    Unknown variables are left as-is.
    """

    def _posix_sub(match: re.Match[str]) -> str:
        var = match.group(1) or match.group(2)
        return os.environ.get(var or "", match.group(0))

    s2 = _POSIX_ENV_PATTERN.sub(_posix_sub, s)
    s2 = _WIN_ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), s2)
    return s2


def resolve_path_like(
    s: str,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    extra_placeholders: dict[str, str] | None = None,
) -> str:
    """Resolve project-style placeholders and env/home expansions.

    Supported placeholders:
    ``{output_dir}``, ``{engine_dir}``. Additional placeholders may be supplied via
    ``extra_placeholders``. Unknown placeholders raise ``ValueError``.
    """

    raw = s.strip()
    base_output = output_dir or project_dirs.output_dir
    base_engine = engine_dir or project_dirs.engine_dir

    placeholders = {
        "{output_dir}": str(base_output),
        "{engine_dir}": str(base_engine),
    }
    if extra_placeholders:
        placeholders.update(extra_placeholders)

    unknown = {placeholder for placeholder in re.findall(r"\{[^}]+\}", raw) if placeholder not in placeholders}
    if unknown:
        raise ValueError(f"Unknown placeholders in path '{s}': {', '.join(sorted(unknown))}")

    for key, value in placeholders.items():
        raw = raw.replace(key, value)

    raw = expand_env_vars(raw)
    return str(Path(raw).expanduser())


# --- USI option path helpers -----------------------------------------------

# Whitelist of USI option keys that are treated as filesystem paths.
# Keep this set aligned with EngineFactory and AsyncUsiEngine option handling.
PATH_OPTION_KEYS: set[str] = {
    "EvalDir",  # YaneuraOu
    "BookDir",  # YaneuraOu
    "Book_File",  # dlshogi
    "DNN_Model",  # dlshogi / FukauraOu
}


def is_path_option_key(name: str) -> bool:
    """Return True if the USI option key should be treated as a path."""
    return name in PATH_OPTION_KEYS


def maybe_resolve_path_option(
    name: str,
    value: object,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> object:
    """Resolve a USI option value only when the key is path-typed.

    Non-string values are returned unchanged. Strings are resolved through
    resolve_path_like using project placeholders and env/home expansion.
    """
    if not isinstance(value, str):
        return value
    if not is_path_option_key(name):
        return value
    return resolve_path_like(value, output_dir=output_dir, engine_dir=engine_dir)
