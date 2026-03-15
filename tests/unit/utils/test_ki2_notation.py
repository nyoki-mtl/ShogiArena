from shogiarena._core.shared.kernel.ki2_notation import normalize_ki2_move_text


def test_normalize_ki2_move_text_rewrites_legacy_prefixes() -> None:
    assert normalize_ki2_move_text("▲７六歩") == "☗７六歩"
    assert normalize_ki2_move_text("△３四歩") == "☖３四歩"


def test_normalize_ki2_move_text_preserves_current_prefixes_and_plain_text() -> None:
    assert normalize_ki2_move_text("☗７六歩") == "☗７六歩"
    assert normalize_ki2_move_text("☖３四歩") == "☖３四歩"
    assert normalize_ki2_move_text("７六歩") == "７六歩"
