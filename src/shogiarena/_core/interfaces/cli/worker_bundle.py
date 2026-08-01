"""Worker bundle CLI command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from shogiarena._core.contexts.game_session.application.preplaced_resource_mapping import (
    PreplacedArtifactSource,
    build_preplaced_resource_mapping,
)
from shogiarena._core.contexts.game_session.application.worker_bundle_builder import build_worker_bundle
from shogiarena._core.contexts.game_session.ports.worker_deployment import WORKER_PYTHON_VERSION
from shogiarena._core.interfaces.cli.main import CliArgumentError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """worker-bundle commandを登録する。"""

    parser = subparsers.add_parser("worker-bundle", help="Build immutable remote worker bundles")
    commands = parser.add_subparsers(dest="worker_bundle_command")
    commands.required = True
    build_parser = commands.add_parser("build", help="Build a content-addressed worker bundle")
    build_parser.add_argument("--output", type=Path, required=True, help="Absolute output .zip path")
    build_parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    build_parser.add_argument("--python-version", default=WORKER_PYTHON_VERSION)
    build_parser.add_argument("--target-os", choices=["linux"], default="linux")
    build_parser.add_argument("--architecture", choices=["x86_64"], default="x86_64")
    build_parser.set_defaults(handler=_build_command)

    preplaced_parser = commands.add_parser(
        "preplaced-map",
        help="Print SHOGIARENA_REMOTE_PREPLACED_RESOURCES JSON",
    )
    preplaced_parser.add_argument(
        "--engine",
        action="append",
        nargs=3,
        required=True,
        metavar=("NAME", "LOCAL_PATH", "REMOTE_PATH"),
        help="Engine name, local binary, and absolute remote path (repeatable)",
    )
    preplaced_parser.add_argument(
        "--resource",
        action="append",
        nargs=3,
        default=[],
        metavar=("ENGINE_NAME", "LOCAL_PATH", "REMOTE_PATH"),
        help="Owning engine name, local file/directory, and absolute remote path (repeatable)",
    )
    preplaced_parser.set_defaults(handler=_preplaced_map_command)


def _build_command(args: argparse.Namespace) -> int:
    result = build_worker_bundle(
        output_path=args.output,
        project_root=args.project_root,
        target_os=args.target_os,
        architecture=args.architecture,
        python_version=args.python_version,
    )
    print(f"deployment_id={result.manifest.deployment_id}")
    print(f"bundle_sha256={result.bundle_sha256}")
    print(f"manifest_sha256={result.manifest.manifest_sha256}")
    print(f"bundle={result.bundle_path}")
    return 0


def _preplaced_map_command(args: argparse.Namespace) -> int:
    try:
        engines = [_source_from_tokens(tokens) for tokens in args.engine]
        resources = [_source_from_tokens(tokens) for tokens in args.resource]
        mapping = build_preplaced_resource_mapping(engines=engines, resources=resources)
    except (OSError, ValueError) as exc:
        raise CliArgumentError(f"cannot build preplaced resource mapping: {exc}") from exc
    print(json.dumps(mapping, sort_keys=True, separators=(",", ":")))
    return 0


def _source_from_tokens(tokens: list[str]) -> PreplacedArtifactSource:
    engine_name, local_path, remote_path = tokens
    if "\\" in remote_path or "\x00" in remote_path or "\n" in remote_path or "\r" in remote_path:
        raise ValueError(f"remote path must use POSIX syntax: {remote_path!r}")
    parsed_remote_path = PurePosixPath(remote_path)
    if not parsed_remote_path.is_absolute():
        raise ValueError(f"remote path must be absolute: {remote_path}")
    return PreplacedArtifactSource(
        engine_name=engine_name,
        local_path=Path(local_path),
        remote_path=str(parsed_remote_path),
    )


__all__ = ["register"]
