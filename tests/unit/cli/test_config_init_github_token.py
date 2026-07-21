"""`config init` が秘密を settings.yaml へ書かせないことの回帰テスト。

`--github-token` は 1.0.0 で `--github-token-env` に置き換わったが、argparse の
前方一致により `--github-token ghp_xxx` が `--github-token-env` として解決され、
トークン文字列が「環境変数名」として平文保存されていた。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.interfaces.cli.main import build_parser


def _init_args(parser_argv: list[str]) -> object:
    return build_parser().parse_args(parser_argv)


@pytest.mark.parametrize("is_non_interactive", [True, False], ids=["non-interactive", "wizard"])
def test_config_init_rejects_removed_github_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_non_interactive: bool,
) -> None:
    """対話・非対話のどちらの経路でも、無警告で捨てずに停止すること。"""

    from shogiarena._core.interfaces.cli.config import command as config_command

    settings_path = tmp_path / "settings.yaml"
    argv = [
        "config",
        "init",
        "--settings",
        str(settings_path),
        "--output-dir",
        str(tmp_path / "out"),
        "--engine-dir",
        str(tmp_path / "engines"),
        "--github-token",
        "ghp_thismustnotbepersisted",
    ]
    if is_non_interactive:
        argv.insert(2, "--non-interactive")
    args = _init_args(argv)

    # wizard 経路を確実に通すため TTY を偽装する。呼ばれたら失敗させる。
    monkeypatch.setattr(config_command.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(
        config_command,
        "run_config_wizard",
        lambda _args: pytest.fail("wizard must not run when --github-token was supplied"),
    )

    with pytest.raises(SystemExit, match="--github-token-env"):
        config_command._config_init(args)

    assert not settings_path.exists()


def test_config_init_accepts_github_token_env(tmp_path: Path) -> None:
    from shogiarena._core.interfaces.cli.config import command as config_command

    settings_path = tmp_path / "settings.yaml"
    args = _init_args(
        [
            "config",
            "init",
            "--non-interactive",
            "--settings",
            str(settings_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--engine-dir",
            str(tmp_path / "engines"),
            "--github-token-env",
            "MY_TOKEN_VAR",
        ]
    )

    config_command._config_init(args)

    written = settings_path.read_text(encoding="utf-8")
    assert "github_token_env: MY_TOKEN_VAR" in written
    assert "ghp_" not in written
