"""トーナメント系コマンドの共通 CLI 引数定義."""

from __future__ import annotations

import argparse


def add_tournament_common_args(
    parser: argparse.ArgumentParser,
    *,
    should_include_config: bool = True,
    should_include_sections: bool = True,
    should_include_tournament: bool = True,
    should_include_generate: bool = False,
    should_include_rating: bool = True,
    should_include_sprt: bool = True,
    should_include_openbench: bool = True,
) -> None:
    """トーナメント系の共通 CLI 引数を追加する。"""

    if should_include_config:
        parser.add_argument("config", nargs="?", help="Path to configuration YAML")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without executing",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and exit without scheduling",
    )
    parser.add_argument(
        "--experiment-name",
        help="Override the experiment name (creates runs/<name>-<hash8>/timestamp)",
    )
    parser.add_argument(
        "--run-dir",
        help="Override the run directory (absolute or relative path)",
    )
    parser.add_argument(
        "--no-resume",
        dest="should_skip_resume",
        action="store_true",
        help="Start a new run instead of resuming",
    )
    parser.add_argument(
        "--provision",
        choices=["none", "force"],
        default="none",
        help="Provision engine assets to SSH instances before execution",
    )
    parser.add_argument(
        "--git-worktree",
        choices=["strict", "clean", "allow-dirty"],
        default="strict",
        help="Control git worktree handling before builds",
    )
    if should_include_sections:
        parser.add_argument(
            "--engine",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Engine definition (repeatable)",
        )
        parser.add_argument(
            "--rules",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override rules.* using YAML-style KEY=VALUE tokens",
        )
        if should_include_tournament:
            parser.add_argument(
                "--tournament",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override tournament.* using YAML-style KEY=VALUE tokens",
            )
        if should_include_generate:
            parser.add_argument(
                "--generate",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override generate.* using YAML-style KEY=VALUE tokens",
            )
        if should_include_rating:
            parser.add_argument(
                "--rating",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override rating.* using YAML-style KEY=VALUE tokens",
            )
        parser.add_argument(
            "--dashboard",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override dashboard.* using YAML-style KEY=VALUE tokens",
        )
        parser.add_argument(
            "--system",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override system.* using YAML-style KEY=VALUE tokens",
        )
        if should_include_sprt:
            parser.add_argument(
                "--sprt",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override sprt.* using YAML-style KEY=VALUE tokens",
            )
        if should_include_openbench:
            parser.add_argument(
                "--openbench",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override openbench.* using YAML-style KEY=VALUE tokens",
            )


def flatten_block_tokens(raw: list[list[str]] | None) -> list[str]:
    """--rules 等の複数指定を 1 次元配列に展開する。"""

    if not raw:
        return []
    flattened: list[str] = []
    for entry in raw:
        flattened.extend(entry)
    return flattened


__all__ = ["add_tournament_common_args", "flatten_block_tokens"]
