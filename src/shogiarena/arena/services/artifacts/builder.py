from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)


def run_make(
    src: Path,
    edition: str,
    target_cpu: str,
    compiler: str,
    target: str,
    jobs: int,
    *,
    log_file: TextIO | None = None,
    executor: Callable[[Sequence[str]], None] | None = None,
) -> None:
    """Run make with YaneuraOu-style variables.

    Args:
        src: Source directory where Makefile exists.
        edition: YANEURAOU_EDITION value.
        target_cpu: TARGET_CPU value (e.g., AVX2).
        compiler: C++ compiler (e.g., clang++).
        target: Make target (e.g., normal, profile, release).
        jobs: Parallel jobs for make.
    Optionally captures stdout/stderr to ``log_file`` when provided.
    """
    # Build base make command; target may differ across repos. Try fallbacks.
    base = [
        "make",
        f"YANEURAOU_EDITION={edition}",
        f"TARGET_CPU={target_cpu}",
        f"COMPILER={compiler}",
    ]
    candidates: list[list[str]] = []
    if target and target.strip():
        candidates.append(base + [target, f"-j{jobs}"])
    # Common fallbacks used by YaneuraOu variants
    for t in ("build", "all"):
        candidates.append(base + [t, f"-j{jobs}"])
    # As a last resort, try default target
    candidates.append(base + [f"-j{jobs}"])

    errors: list[str] = []
    for cmd in candidates:
        try:
            if executor is not None:
                executor(cmd)
                return
            if log_file is None:
                logger.info("$ %s", " ".join(cmd))
                subprocess.check_call(cmd, cwd=str(src))
            else:
                log_file.write(f"$ {' '.join(cmd)}\n")
                log_file.flush()
                subprocess.check_call(cmd, cwd=str(src), stdout=log_file, stderr=subprocess.STDOUT)
            return
        except subprocess.CalledProcessError as e:
            errors.append(f"{cmd} -> {e.returncode}")
            continue
    raise subprocess.CalledProcessError(1, candidates[-1], output="\n".join(errors))


def maybe_build_from_yaml(config_yaml: Path) -> Path | None:
    """No-op: top-level builds are deprecated; builds handled by ArtifactResolver.

    Left for CLI compatibility.
    """
    return None
