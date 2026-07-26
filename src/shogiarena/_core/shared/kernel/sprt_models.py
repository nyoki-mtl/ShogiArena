"""SPRT の統計モデル識別子。

resume の test 定義（``run_artifact_hashes``）に含まれるため、値は安定させる。

shared kernel に置くのは、モデルを選ぶ側（game_session）と、resume で観測を
replay する側（tournament の state store）の双方が識別子を必要とするため。
"""

from __future__ import annotations

SPRT_MODEL_GSPRT_TRINOMIAL = "gsprt-trinomial-v1"
SPRT_MODEL_GSPRT_PENTANOMIAL = "gsprt-pentanomial-v1"

SUPPORTED_SPRT_MODELS = frozenset({SPRT_MODEL_GSPRT_TRINOMIAL, SPRT_MODEL_GSPRT_PENTANOMIAL})

__all__ = [
    "SPRT_MODEL_GSPRT_PENTANOMIAL",
    "SPRT_MODEL_GSPRT_TRINOMIAL",
    "SUPPORTED_SPRT_MODELS",
]
