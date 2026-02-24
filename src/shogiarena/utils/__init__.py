"""ShogiArena ユーティリティ群のルートパッケージ。

主要モジュール:
- ``cpuinfo``: CPU検出と feature 判定
- ``common``: 定数・パス解決・ロギング
- ``types``: ゲーム結果やハンデ定義などの型
"""

from . import common, cpuinfo, types  # re-export only curated modules

__all__ = ["common", "cpuinfo", "types"]
