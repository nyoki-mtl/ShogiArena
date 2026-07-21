"""Remove build/test cache artifacts.

``find`` / ``rm`` に依存せず Windows でも動くようにするための ``make clean`` 実装。
"""

from __future__ import annotations

import shutil
from pathlib import Path

# 走査から除外するディレクトリ（参照リポジトリや仮想環境まで消さない）。
SKIP_DIRS = {".git", ".venv", "_refs", "node_modules", ".sandbox"}

CACHE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ty",
    ".ruff_cache",
    "htmlcov",
}
CACHE_FILE_NAMES = {".coverage"}
CACHE_FILE_SUFFIXES = {".pyc"}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    removed_dirs = 0
    removed_files = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_dir():
            if path.name in CACHE_DIRS:
                shutil.rmtree(path, ignore_errors=True)
                removed_dirs += 1
        elif path.name in CACHE_FILE_NAMES or path.suffix in CACHE_FILE_SUFFIXES:
            path.unlink(missing_ok=True)
            removed_files += 1

    print(f"Removed {removed_dirs} cache directories and {removed_files} cache files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
