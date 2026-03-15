from __future__ import annotations

_KI2_PREFIX_MAP = {
    "▲": "☗",
    "△": "☖",
}


def normalize_ki2_move_text(text: str) -> str:
    if not text:
        return text
    return f"{_KI2_PREFIX_MAP.get(text[0], text[0])}{text[1:]}"
