from __future__ import annotations

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object


def test_is_str_object_mapping_accepts_str_key_mapping_only() -> None:
    assert is_str_object_mapping({"gid": "g1"}) is True
    assert is_str_object_mapping({1: "g1"}) is False
    assert is_str_object_mapping([("gid", "g1")]) is False


def test_to_json_object_normalizes_keys_and_values() -> None:
    payload = to_json_object({"gid": "g1", "clock": {"black": 1000}, "moves": ("7g7f", "3c3d")})

    assert payload["gid"] == "g1"
    assert payload["clock"] == {"black": 1000}
    assert payload["moves"] == ["7g7f", "3c3d"]
