# Arena Dashboard SPSA SSE Contract

SPSA 画面は Server-Sent Events (SSE) を継続利用します（Live View とは別系統）。

- API: `src/shogiarena/web/dashboard/backend/spsa/api.py`
- ストリーミング実装: `src/shogiarena/web/dashboard/backend/spsa/streams.py`
- フロント購読: `src/shogiarena/web/dashboard/frontend/src/modules/spsa/services/streams.ts`

各 SSE エンドポイントの event 名 / payload 形は実装を source of truth とします。

## 設計方針

- 同一オリジンの EventSource はブラウザ側に接続数上限がある（目安 6 本）。
- タブ別に **必要なストリームだけ** を開き、不要な SSE を常時接続しない。
- 解析系は **集約ストリーム** を優先し、同期更新と接続数削減を両立する。
- 既存ストリームを廃止する場合は段階移行し、REST との互換を維持する。

## Convergence 集約 SSE

Convergence タブに表示する内容は `spsa_convergence` SSE で集約配信する。

- `convergence_update` payload に以下を同梱する:
  - `ltc_results`: LTC 回帰結果（summary + results）
  - `mobility_series`: Mobility 用の系列（`gain_ak`, `variant_indices`）
- 目的:
  - Convergence タブの更新を **単一 SSE** で同期
  - LTC Elo / Mobility / Δ-norm を同一タイミングで更新

`/api/spsa/ltc/results/stream` は互換用途で残すが、Convergence タブの描画は集約 SSE を優先する。
