"""PowerShell スクリプトが UTF-8 BOM 付きで保存されていることを検査する。

Makefile と CI は Windows PowerShell 5.1（`powershell.exe`）でも release 系スクリプトを
起動する。5.1 は BOM が無いファイルを ANSI として読むため、日本語コメントを含む
スクリプトは BOM を失った瞬間に parse error になる。壊れ方が「実行するまで分からない」
ため、静的に検査する。
"""

from __future__ import annotations

import sys
from pathlib import Path

_UTF8_BOM = b"\xef\xbb\xbf"
_SEARCH_ROOTS = ("scripts", "tools")


def _iter_powershell_files(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for root in _SEARCH_ROOTS:
        found.extend(sorted((repo_root / root).rglob("*.ps1")))
    return found


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    files = _iter_powershell_files(repo_root)
    if not files:
        print("No PowerShell scripts found; the encoding check would be vacuous.", file=sys.stderr)
        return 1

    violations = [path for path in files if not path.read_bytes().startswith(_UTF8_BOM)]
    for path in violations:
        print(
            f"{path.relative_to(repo_root).as_posix()}: missing UTF-8 BOM. "
            "Windows PowerShell 5.1 reads it as ANSI and fails to parse non-ASCII content.",
            file=sys.stderr,
        )
    if violations:
        return 1

    print(f"PowerShell encoding check passed: {len(files)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
