from __future__ import annotations

from collections.abc import Sequence

from shogiarena._core.shared.kernel.json_types import JsonObject


def build_max_ply_sync_options(
    *,
    is_max_plies_enabled: bool,
    should_sync_max_plies_with_engine: bool,
    max_plies: int | None,
    engine_max_ply_option_names: str | Sequence[str],
) -> JsonObject:
    if not is_max_plies_enabled or not should_sync_max_plies_with_engine or max_plies is None:
        return {}

    option_names: list[str] = []
    names_cfg = engine_max_ply_option_names
    if isinstance(names_cfg, str):
        if names_cfg.lower() == "auto":
            option_names = ["MaxMovesToDraw", "Draw_Ply"]
        else:
            option_names = [part.strip() for part in names_cfg.replace("|", ",").split(",") if part.strip()]
    else:
        for entry in names_cfg:
            option_names.extend(part.strip() for part in str(entry).replace("|", ",").split(",") if part.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for option_name in option_names:
        if option_name and option_name not in seen:
            seen.add(option_name)
            deduped.append(option_name)
    if not deduped:
        return {}
    return {"|".join(deduped): int(max_plies)}


__all__ = ["build_max_ply_sync_options"]
