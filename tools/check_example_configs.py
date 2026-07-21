"""公開example設定をproduction CLIのdry-runで検証する。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypedDict


class ExampleEntry(TypedDict):
    """検証対象の公開example entry。"""

    config: str
    command: list[str]


def _load_manifest(path: Path) -> list[ExampleEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("example validation manifest must be a list")
    entries: list[ExampleEntry] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ValueError(f"manifest entry {index} must be an object")
        config = raw.get("config")
        command = raw.get("command")
        if not isinstance(config, str) or not config:
            raise ValueError(f"manifest entry {index}.config must be a non-empty string")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError(f"manifest entry {index}.command must be a non-empty string list")
        entries.append({"config": config, "command": command})
    return entries


def main() -> int:
    """Manifestに列挙したexampleを公開CLIで検証する。"""

    root = Path(__file__).resolve().parents[1]
    manifest = root / "examples" / "configs" / "validation-manifest.json"
    entries = _load_manifest(manifest)
    failures: list[str] = []
    cli_bootstrap = "import sys; from shogiarena.cli import main; main(sys.argv[1:])"

    for entry in entries:
        config_path = root / entry["config"]
        if not config_path.is_file():
            failures.append(f"missing config: {entry['config']}")
            continue
        command = [sys.executable, "-c", cli_bootstrap, *entry["command"], str(config_path), "--dry-run"]
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode != 0:
            failures.append(f"dry-run failed: {' '.join(entry['command'])} {entry['config']}")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print(f"Validated {len(entries)} public example configs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
