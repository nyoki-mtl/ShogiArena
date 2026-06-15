"""`select_overlay_options` のフラット形式廃止挙動を検証する。"""

from __future__ import annotations

import pytest

from shogiarena._core.shared.kernel.overlay_options import select_overlay_options


def test_nested_options_block_returned() -> None:
    raw = {"options": {"Threads": 4, "Hash": 16}}
    assert select_overlay_options(raw, source="x.yaml") == {"Threads": 4, "Hash": 16}


def test_engine_only_overlay_returns_empty() -> None:
    raw = {"engine": {"isready_lock_skip_if_exists": True}}
    assert select_overlay_options(raw, source="x.yaml") == {}


def test_explicit_null_options_returns_empty() -> None:
    raw = {"options": None, "engine": {"isready_lock_skip_if_exists": True}}
    assert select_overlay_options(raw, source="x.yaml") == {}


def test_empty_mapping_returns_empty() -> None:
    assert select_overlay_options({}, source="x.yaml") == {}


def test_flat_form_raises() -> None:
    raw = {"Threads": 4, "NetworkDelay": 0}
    with pytest.raises(TypeError, match="flat top-level form is no longer supported"):
        select_overlay_options(raw, source="legacy.yaml")


def test_non_mapping_options_raises() -> None:
    raw = {"options": [1, 2, 3]}
    with pytest.raises(TypeError, match="overlay options must be a mapping"):
        select_overlay_options(raw, source="x.yaml")
