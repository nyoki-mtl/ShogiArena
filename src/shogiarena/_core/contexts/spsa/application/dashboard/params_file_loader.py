"""Load and discover SPSA parameter files for dashboard projections."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float

_NOT_USED_MARKER = "[[NOT USED]]"


def _parse_required_float(raw: str, *, path: Path, line_no: int, field: str) -> float:
    parsed = coerce_float(raw)
    if parsed is None:
        raise ValueError(f"invalid {field}: {path}({line_no}): {raw!r}")
    return parsed


def read_params_file(path: Path) -> list[ParamEntry]:
    """Parse parameter entries from ``path``."""

    entries: list[ParamEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            line = raw.rstrip()
            if not line:
                continue
            is_not_used = False
            if _NOT_USED_MARKER in line:
                line = line.replace(_NOT_USED_MARKER, "")
                is_not_used = True
            if "//" in line:
                value_part, comment = line.split("//", 1)
            else:
                value_part, comment = line, ""
            values = [value.strip() for value in value_part.split(",")]
            if len(values) < 7:
                raise ValueError(f"insufficient params: {path}({line_no}): {raw}")
            entries.append(
                ParamEntry(
                    name=values[0],
                    type=values[1],
                    value=_parse_required_float(values[2], path=path, line_no=line_no, field="value"),
                    min=_parse_required_float(values[3], path=path, line_no=line_no, field="min"),
                    max=_parse_required_float(values[4], path=path, line_no=line_no, field="max"),
                    step=_parse_required_float(values[5], path=path, line_no=line_no, field="step"),
                    delta=_parse_required_float(values[6], path=path, line_no=line_no, field="delta"),
                    comment=comment.strip(),
                    is_not_used=is_not_used,
                )
            )
    return entries


def resolve_params_path(*, parameters_path: str | None, run_dir: Path) -> Path | None:
    """Resolve best-matching ``*.params`` path from metadata and run directory."""

    candidates: list[Path] = []
    if parameters_path:
        candidates.append(Path(parameters_path))
    candidates.extend(_candidate_param_paths(run_dir))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _candidate_param_paths(run_dir: Path) -> list[Path]:
    search_roots = [
        run_dir / "spsa" / "params",
        run_dir / "spsa",
        run_dir,
    ]
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue
        for path in root.glob("*.params"):
            if path not in seen:
                candidates.append(path)
                seen.add(path)
    return candidates


__all__ = ["read_params_file", "resolve_params_path"]
