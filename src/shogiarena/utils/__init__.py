"""ShogiArena ユーティリティ群のルートパッケージ。

主要モジュール:
- ``board``: SFENの正規化や局面生成
- ``cpuinfo``: CPU検出と feature 判定
- ``common``: 定数・パス解決・ロギング
- ``types``: ゲーム結果やハンデ定義などの型
"""

from . import board, common, cpuinfo, types  # re-export only curated modules

__all__ = ["board", "common", "cpuinfo", "types"]
