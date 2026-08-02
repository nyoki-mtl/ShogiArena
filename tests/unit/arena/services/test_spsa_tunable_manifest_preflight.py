from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.spsa.adapters.tunable_manifest_preflight import (
    TUNABLE_HANDSHAKE_FILENAME,
    run_tunable_manifest_preflight,
    validate_sealed_tunable_evidence,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption


def _write_space(tmp_path: Path, *, required: bool = True, explicit: bool = False) -> Path:
    parameters = (
        """
parameters:
  - id: threads
    target: {option: Tune.Threads, value_encoding: integer}
    value_type: int
    initial: 4
    bounds: {min: 2, max: 8}
    schedule: {c_end: 1, r_end: 1}
"""
        if explicit
        else "select: [threads]\n"
    )
    path = tmp_path / "space.yaml"
    path.write_text(
        (
            "schema_version: shogiarena.spsa.space.v1\n"
            "target:\n"
            "  protocol: usi_options\n"
            "  tunable_manifest:\n"
            f"    required: {'true' if required else 'false'}\n"
            "    command: usi_tunables\n"
            f"{parameters}"
        ),
        encoding="utf-8",
    )
    return path


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "shogiarena.usi_tunables.v1",
        "tunables": [
            {
                "id": "threads",
                "option": "Tune.Threads",
                "value_type": "int",
                "encoding": "integer",
                "default": 4,
                "min": 1,
                "max": 16,
                "schedule": {"c_end": 1, "r_end": 1},
            }
        ],
    }


class _Engine:
    def __init__(self, manifest: dict[str, object] | None, *, should_timeout: bool = False) -> None:
        self.manifest = manifest
        self.should_timeout = should_timeout
        self.started = False
        self.closed = False
        self.request: tuple[str, float | None] | None = None
        self.options = {
            "Tune.Threads": UsiOption(
                name="Tune.Threads",
                option_type="spin",
                default="4",
                minimum=1,
                maximum=16,
            ),
            "Clear Hash": UsiOption(name="Clear Hash", option_type="button"),
        }

    async def start(self) -> None:
        self.started = True

    async def request_tunable_manifest(self, *, command: str, timeout: float | None = None):
        self.request = (command, timeout)
        if self.should_timeout:
            raise TimeoutError
        return self.manifest

    def get_usi_options(self):
        return self.options

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self, engine: _Engine) -> None:
        self.engine = engine
        self.kwargs: dict[str, object] = {}

    async def create_engine(self, _path: Path, **kwargs: object) -> _Engine:
        self.kwargs = kwargs
        return self.engine


class _SequenceFactory:
    def __init__(self, engines: list[_Engine]) -> None:
        self.engines = iter(engines)

    async def create_engine(self, _path: Path, **_kwargs: object) -> _Engine:
        return next(self.engines)


def _config(space_path: Path, *, instance_id: str | None = None):
    return SimpleNamespace(
        space_path=str(space_path),
        baseline=[],
        tuned=[
            SimpleNamespace(
                engine_path=space_path.parent / "engine.yaml",
                name="tuned",
                instance_id=instance_id,
                cpu_affinity=None,
                handshake_timeout=2.0,
            )
        ],
        system=SimpleNamespace(engine_handshake_timeout=3.0),
        variants=SimpleNamespace(apply=SimpleNamespace(is_clear_hash_enabled=True)),
    )


@pytest.mark.asyncio
async def test_manifest_preflight_seals_runtime_options_and_normalized_space(tmp_path: Path) -> None:
    engine = _Engine(_manifest())
    factory = _Factory(engine)
    space, evidence = await run_tunable_manifest_preflight(
        config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
        run_dir=tmp_path,
        engine_factory_service=factory,  # type: ignore[arg-type]
        instance_pool=InstancePool(),
    )

    assert space.parameters[0].option == "Tune.Threads"
    assert evidence["runtime_scope"] == "local_runtime"
    assert engine.started is True
    assert engine.request == ("usi_tunables", 2.0)
    assert engine.closed is True
    persisted = json.loads((tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).read_text(encoding="utf-8"))
    rebuilt, _ = validate_sealed_tunable_evidence(
        space_path=tmp_path / "space.yaml",
        normalized_space_path=tmp_path / "spsa" / "space.normalized.json",
        evidence=persisted,
        clear_hash_required=True,
    )
    assert rebuilt.to_json() == space.to_json()


@pytest.mark.asyncio
async def test_received_manifest_rejects_explicit_space_identity_mismatch(tmp_path: Path) -> None:
    space_path = _write_space(tmp_path, explicit=True)
    space_path.write_text(
        space_path.read_text(encoding="utf-8").replace("Tune.Threads", "Tune.Other"),
        encoding="utf-8",
    )
    engine = _Engine(_manifest())
    engine.options["Tune.Other"] = UsiOption(
        name="Tune.Other",
        option_type="spin",
        default="4",
        minimum=1,
        maximum=16,
    )

    with pytest.raises(ValueError, match="option conflicts with tunable manifest"):
        await run_tunable_manifest_preflight(
            config=_config(space_path),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_received_manifest_rejects_explicit_space_bounds_mismatch(tmp_path: Path) -> None:
    space_path = _write_space(tmp_path, explicit=True)
    space_path.write_text(
        space_path.read_text(encoding="utf-8").replace("bounds: {min: 2, max: 8}", "bounds: {min: 0, max: 8}"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bounds exceed tunable manifest bounds"):
        await run_tunable_manifest_preflight(
            config=_config(space_path),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(_Engine(_manifest())),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )


def test_sealed_tunable_evidence_rejects_corrupt_normalized_space(tmp_path: Path) -> None:
    space_path = _write_space(tmp_path)
    manifest = _manifest()
    space = {
        "schema_version": "shogiarena.spsa.space.v1",
        "target": {
            "engine_family": None,
            "protocol": "usi_options",
            "required_options_policy": "strict",
            "tunable_manifest": {"required": True, "command": "usi_tunables"},
        },
        "parameters": [
            {
                "id": "threads",
                "target": {"option": "Tune.Threads", "value_encoding": "integer"},
                "value_type": "int",
                "initial": 4.0,
                "bounds": {"min": 1.0, "max": 16.0},
                "schedule": {"c_end": 1.0, "r_end": 1.0},
                "rounding": {"mode": "none"},
                "significant_digits": 9,
            }
        ],
    }
    normalized_path = tmp_path / "spsa" / "space.normalized.json"
    normalized_path.parent.mkdir(parents=True)
    normalized_path.write_text(json.dumps({**space, "parameters": []}), encoding="utf-8")
    evidence = {
        "schema_version": "shogiarena.spsa.tunable-handshake.v1",
        "status": "passed",
        "manifest": manifest,
        "advertised_options": [
            {
                "name": "Tune.Threads",
                "type": "spin",
                "default": "4",
                "minimum": 1,
                "maximum": 16,
                "choices": [],
            }
        ],
        "normalized_space": space,
    }

    with pytest.raises(ValueError, match="normalized space artifact mismatch"):
        validate_sealed_tunable_evidence(
            space_path=space_path,
            normalized_space_path=normalized_path,
            evidence=evidence,
            clear_hash_required=False,
        )


@pytest.mark.asyncio
async def test_clear_hash_enabled_requires_usi_button(tmp_path: Path) -> None:
    engine = _Engine(_manifest())
    engine.options.pop("Clear Hash")

    with pytest.raises(ValueError, match="requires the tuned engine to advertise 'Clear Hash'"):
        await run_tunable_manifest_preflight(
            config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_clear_hash_enabled_validates_baseline_engine_role(tmp_path: Path) -> None:
    tuned = _Engine(_manifest())
    baseline = _Engine(None)
    baseline.options.pop("Clear Hash")
    config = _config(_write_space(tmp_path))
    config.baseline = [
        SimpleNamespace(
            engine_path=tmp_path / "baseline.yaml",
            name="baseline",
            instance_id=None,
            cpu_affinity=None,
            handshake_timeout=2.0,
        )
    ]

    with pytest.raises(ValueError, match=r"baseline\[0\].*'Clear Hash'"):
        await run_tunable_manifest_preflight(
            config=config,  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_SequenceFactory([tuned, baseline]),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert tuned.closed is True
    assert baseline.closed is True
    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_clear_hash_disabled_allows_engine_without_button(tmp_path: Path) -> None:
    engine = _Engine(_manifest())
    engine.options.pop("Clear Hash")
    config = _config(_write_space(tmp_path))
    config.variants.apply.is_clear_hash_enabled = False

    _space, evidence = await run_tunable_manifest_preflight(
        config=config,  # type: ignore[arg-type]
        run_dir=tmp_path,
        engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
        instance_pool=InstancePool(),
    )

    assert evidence["status"] == "passed"


@pytest.mark.asyncio
async def test_required_manifest_timeout_fails_before_artifact_write(tmp_path: Path) -> None:
    engine = _Engine(None, should_timeout=True)
    with pytest.raises(TimeoutError):
        await run_tunable_manifest_preflight(
            config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert engine.closed is True
    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_required_manifest_missing_fails_before_artifact_write(tmp_path: Path) -> None:
    engine = _Engine(None)
    with pytest.raises(ValueError, match="required"):
        await run_tunable_manifest_preflight(
            config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert engine.closed is True
    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_required_manifest_version_mismatch_fails_before_artifact_write(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["schema_version"] = "shogiarena.usi_tunables.v2"
    engine = _Engine(manifest)
    with pytest.raises(ValueError, match="schema_version"):
        await run_tunable_manifest_preflight(
            config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )

    assert engine.closed is True
    assert not (tmp_path / "spsa" / TUNABLE_HANDSHAKE_FILENAME).exists()


@pytest.mark.asyncio
async def test_optional_manifest_timeout_allows_explicit_space(tmp_path: Path) -> None:
    engine = _Engine(None, should_timeout=True)
    _space, evidence = await run_tunable_manifest_preflight(
        config=_config(_write_space(tmp_path, required=False, explicit=True)),  # type: ignore[arg-type]
        run_dir=tmp_path,
        engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
        instance_pool=InstancePool(),
    )

    assert evidence["response_status"] == "timeout_optional"


@pytest.mark.asyncio
async def test_selected_option_must_be_advertised_and_within_spin_bounds(tmp_path: Path) -> None:
    engine = _Engine(_manifest())
    engine.options = {
        "Tune.Threads": UsiOption(
            name="Tune.Threads",
            option_type="spin",
            minimum=3,
            maximum=6,
        )
    }

    with pytest.raises(ValueError, match="bounds exceed"):
        await run_tunable_manifest_preflight(
            config=_config(_write_space(tmp_path)),  # type: ignore[arg-type]
            run_dir=tmp_path,
            engine_factory_service=_Factory(engine),  # type: ignore[arg-type]
            instance_pool=InstancePool(),
        )


@pytest.mark.asyncio
async def test_remote_instance_uses_same_handshake_contract(tmp_path: Path) -> None:
    engine = _Engine(_manifest())
    factory = _Factory(engine)
    _space, evidence = await run_tunable_manifest_preflight(
        config=_config(_write_space(tmp_path), instance_id="worker-1"),  # type: ignore[arg-type]
        run_dir=tmp_path,
        engine_factory_service=factory,  # type: ignore[arg-type]
        instance_pool=InstancePool(),
    )

    assert factory.kwargs["instance_id"] == "worker-1"
    assert evidence["runtime_scope"] == "remote_runtime"
