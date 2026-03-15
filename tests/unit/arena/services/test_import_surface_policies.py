"""Import-surface policy regression tests for runtime decomposition."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_path",
    [
        "shogiarena._core.contexts.game_session.application",
        "shogiarena._core.contexts.game_session.application.summary",
        "shogiarena._core.contexts.game_session.application.progress",
        "shogiarena._core.contexts.game_session.application.session",
        "shogiarena._core.contexts.game_session.application.completion",
        "shogiarena._core.contexts.game_session.application.engine",
        "shogiarena._core.contexts.game_session.application.orchestration",
    ],
)
def test_application_subpackage_init_exports_are_explicitly_empty(module_path: str) -> None:
    module = importlib.import_module(module_path)
    assert getattr(module, "__all__", []) == []


def test_shared_service_ports_is_canonical_import_surface() -> None:
    module = importlib.import_module("shogiarena._core.shared.kernel.service_ports")
    assert hasattr(module, "DatabaseServicePort")
    assert hasattr(module, "RatingServicePort")
    assert hasattr(module, "SprtServicePort")
    assert hasattr(module, "GameRecordPlayers")


@pytest.mark.parametrize(
    "module_path",
    [
        "shogiarena._core.contexts.game_session.ports.services",
        "shogiarena._core.contexts.tournament.ports.services",
    ],
)
def test_context_service_port_wrappers_are_removed(module_path: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_path)
