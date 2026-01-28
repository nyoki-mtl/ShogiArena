# Arena Dashboard

`src/shogiarena/web/dashboard/` には Arena のダッシュボード用バックエンド資産とフロントエンドがまとまっています。主な構成は次の通りです。

- `api_server.py` / `assets_writer.py` … ダッシュボード向け API サーバと静的アセット出力
- `frontend/` … Vite + Tailwind 製ダッシュボード UI（詳細は `frontend/README.md` を参照）
- `docs/` … ダッシュボード関連ドキュメント（契約・設計メモなど）

## Documentation Map

- `docs/ws_contract.md` — Live View WebSocket (`/ws`) のペイロード契約。
- `docs/spsa_sse_contract.md` — SPSA 用 SSE の契約（Live View とは別系統）。
- `frontend/docs/architecture.md` — フロントエンドモジュール構成と Window API の整理。
- `frontend/docs/typing.md` — 型定義ポリシー。
- `frontend/docs/testing.md` — Live 名前空間テストヘルパの使い方。

フロントエンドの開発フローやコマンドは `frontend/README.md` を参照してください。
