"""Book タブ API ハンドラ。Task 0018。

記録済み DB から book fingerprint/path 単位の集計（provenance / 勝率 / 先後別 /
engine 別 / out-of-book ply 分布）を提供する。out-of-book 算出はクエリ
``out_of_book=1`` で opt-in（book file を開けるため重い。既定 off）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.book.service import build_book_tab_payload
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardGameQueryPort
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


class BookAPI:
    """内蔵定跡(A) の集計ビューを公開する。"""

    def __init__(self, *, db_path: Path, game_query: DashboardGameQueryPort) -> None:
        self._db_path = db_path
        self._game_query = game_query

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/book/summary", self.get_summary)

    async def get_summary(self, request: web.Request) -> web.StreamResponse:
        # out_of_book: 未指定/falsy=算出しない, truthy=bounded 算出（大規模 book はスキップ）,
        # "full"=大規模 book も validate_full して算出（高コスト opt-in）。
        oob_param = request.query.get("out_of_book", "").strip().lower()
        allow_full = oob_param == "full"
        compute_oob = allow_full or oob_param in _TRUTHY
        try:
            payload = build_book_tab_payload(
                self._game_query,
                self._db_path,
                compute_out_of_book_plies=compute_oob,
                allow_full_validation=allow_full,
            )
        except (OSError, ValueError) as exc:
            logger.warning("Failed to build book summary payload: %s", exc)
            return json_error_response("failed to build book summary", status=500)
        return web.json_response(payload)


__all__ = ["BookAPI"]
