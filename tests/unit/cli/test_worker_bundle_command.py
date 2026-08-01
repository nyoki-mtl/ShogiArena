from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shogiarena._core.interfaces.cli.main import CliArgumentError, build_parser


def test_preplaced_map_prints_runtime_logical_ids_and_digests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = tmp_path / "engine"
    engine.write_bytes(b"engine")
    resource = tmp_path / "eval"
    resource.mkdir()
    (resource / "weights.bin").write_bytes(b"weights")
    parser = build_parser()
    args = parser.parse_args(
        [
            "worker-bundle",
            "preplaced-map",
            "--engine",
            "alpha",
            str(engine),
            "/opt/engines/alpha",
            "--resource",
            "alpha",
            str(resource),
            "/opt/eval/alpha",
        ]
    )

    assert args.handler(args) == 0

    mapping = json.loads(capsys.readouterr().out)
    engine_digest = hashlib.sha256(b"engine").hexdigest()
    assert mapping["alpha-linux"] == {
        "path": "/opt/engines/alpha",
        "sha256": engine_digest,
    }
    resource_ids = [logical_id for logical_id in mapping if logical_id.startswith("alpha-linux-resource-")]
    assert len(resource_ids) == 1
    resource_id = resource_ids[0]
    assert resource_id.endswith(mapping[resource_id]["sha256"][:12])
    assert mapping[resource_id]["path"] == "/opt/eval/alpha"


def test_preplaced_map_rejects_relative_remote_path(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.write_bytes(b"engine")
    parser = build_parser()
    args = parser.parse_args(
        [
            "worker-bundle",
            "preplaced-map",
            "--engine",
            "alpha",
            str(engine),
            "relative/engine",
        ]
    )

    with pytest.raises(CliArgumentError, match="remote path must be absolute"):
        args.handler(args)


def test_preplaced_map_rejects_resource_for_undeclared_engine(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.write_bytes(b"engine")
    resource = tmp_path / "book.bin"
    resource.write_bytes(b"book")
    parser = build_parser()
    args = parser.parse_args(
        [
            "worker-bundle",
            "preplaced-map",
            "--engine",
            "alpha",
            str(engine),
            "/opt/engines/alpha",
            "--resource",
            "beta",
            str(resource),
            "/opt/books/book.bin",
        ]
    )

    with pytest.raises(CliArgumentError, match="undeclared engine: beta"):
        args.handler(args)
