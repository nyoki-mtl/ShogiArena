from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast, get_type_hints

import pytest

import shogiarena.engine
import shogiarena.tournament
from shogiarena.engine import UsiEngineConfig
from shogiarena.tournament import GameSpec, TournamentRunResult

# 位置引数での構築を許したまま v1 に入れると、field 追加が破壊的変更になる。
# 例外は provisional 面の単純な構築子に限り、理由をここに残す。
_POSITIONAL_CONSTRUCTION_EXEMPTIONS = {
    # provisional: run ディレクトリ 1 つだけを取る構築子で、field 追加の予定がない。
    "FilesystemRunStorage",
    # provisional: composition root の内部組み立て結果で、利用者は build_default_root() 経由で得る。
    "DefaultRoot",
}


def _public_dataclasses(module: ModuleType) -> list[tuple[str, type[object]]]:
    found: list[tuple[str, type[object]]] = []
    for name in module.__all__:
        exported = getattr(module, name)
        if isinstance(exported, type) and dataclasses.is_dataclass(exported):
            found.append((name, exported))
    return found


def _assert_keyword_only(model: type[object]) -> None:
    signature = inspect.signature(model)

    assert signature.parameters
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in signature.parameters.values())


@pytest.mark.parametrize("model", [UsiEngineConfig, GameSpec])
def test_public_dataclass_constructors_are_keyword_only(model: type[object]) -> None:
    _assert_keyword_only(model)


@pytest.mark.parametrize("module", [shogiarena.engine, shogiarena.tournament], ids=lambda m: m.__name__)
def test_every_exported_dataclass_is_keyword_only(module: ModuleType) -> None:
    exported = _public_dataclasses(module)
    assert exported, f"{module.__name__} exports no dataclass; the ratchet would silently pass"

    for name, model in exported:
        if name in _POSITIONAL_CONSTRUCTION_EXEMPTIONS:
            continue
        _assert_keyword_only(model)


def test_stable_result_field_types_are_importable_from_the_same_module() -> None:
    """stable 宣言した戻り値の field 型は、同じ public module から import できること。"""

    hints = get_type_hints(TournamentRunResult)
    exported = set(shogiarena.tournament.__all__)

    for field in dataclasses.fields(TournamentRunResult):
        hint = hints[field.name]
        for referenced in (hint, *getattr(hint, "__args__", ())):
            name = getattr(referenced, "__name__", None)
            if name is None or referenced.__module__.startswith(("builtins", "collections", "datetime", "pathlib")):
                continue
            assert name in exported, f"TournamentRunResult.{field.name} references {name} outside shogiarena.tournament"


def test_usi_engine_config_constructor_hides_private_provenance() -> None:
    signature = inspect.signature(UsiEngineConfig)

    assert "_raw_engine_path" not in signature.parameters
    assert "_raw_working_directory" not in signature.parameters
    assert "_raw_options" not in signature.parameters
    constructor = cast(Callable[..., UsiEngineConfig], UsiEngineConfig)
    with pytest.raises(TypeError):
        constructor(name="engine", engine_path="engine.exe", _raw_engine_path="hidden")


def test_usi_engine_config_preserves_provenance_across_public_operations(tmp_path: Path) -> None:
    first_output_dir = tmp_path / "first-output"
    second_output_dir = tmp_path / "second-output"
    engine_dir = tmp_path / "engines"
    config = UsiEngineConfig.from_mapping(
        {
            "name": "engine",
            "engine_path": "{engine_dir}/engine.exe",
            "working_directory": "{output_dir}/work",
            "options": {"EvalDir": "{output_dir}/eval"},
        }
    )

    first = config.resolve_paths(output_dir=first_output_dir, engine_dir=engine_dir)
    overridden = first.with_overrides(options={"EvalDir": "{output_dir}/book"}, output_dir=first_output_dir)
    second = overridden.resolve_paths(output_dir=second_output_dir, engine_dir=engine_dir)

    assert second.engine_path == str((engine_dir / "engine.exe").resolve())
    assert second.working_directory == str((second_output_dir / "work").resolve())
    assert second.options["EvalDir"] == str((second_output_dir / "book").resolve())


def test_direct_usi_engine_config_construction_initializes_provenance(tmp_path: Path) -> None:
    config = UsiEngineConfig(
        name="engine",
        engine_path="{engine_dir}/engine.exe",
        options={"EvalDir": "{output_dir}/eval"},
    )

    resolved = config.resolve_paths(output_dir=tmp_path / "output", engine_dir=tmp_path / "engines")

    assert resolved.engine_path == str((tmp_path / "engines" / "engine.exe").resolve())
    assert resolved.options["EvalDir"] == str((tmp_path / "output" / "eval").resolve())


def test_game_spec_rejects_positional_construction() -> None:
    constructor = cast(Callable[..., GameSpec], GameSpec)
    with pytest.raises(TypeError):
        constructor("black", "white", "startpos", "g0001-test")
