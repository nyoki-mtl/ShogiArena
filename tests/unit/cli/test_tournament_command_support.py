from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.cli.run import tournament_command_support


@pytest.mark.asyncio
async def test_run_tournament_like_normalizes_missing_nested_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "tournament.yaml"
    config_path.write_text("engines: []\nrules: {}\n", encoding="utf-8")
    args = SimpleNamespace(
        config=str(config_path),
        experiment_name=None,
        run_dir=None,
        engine=None,
        rules=None,
        tournament=None,
        rating=None,
        dashboard=None,
        logging=None,
        system=None,
        sprt=None,
        openbench=None,
        path_preflight="off",
    )
    monkeypatch.setattr(
        tournament_command_support,
        "parse_tournament_config_file",
        lambda _path: {"engines": [], "rules": {}},
    )
    monkeypatch.setattr(
        tournament_command_support,
        "build_tournament_run_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing overlay")),
    )

    with pytest.raises(CliError, match="Invalid tournament config: missing overlay"):
        await tournament_command_support.run_tournament_like(args, should_require_sprt=False)
