# Dashboard SSE Protocol

最終更新: 2026-02

このドキュメントは Dashboard の Server-Sent Events (SSE) プロトコル仕様をまとめます。

## 概要

ダッシュボードは以下の機能で SSE を使用します：

- **Tournament Summary**: トーナメント進捗とスタンディング
- **SPSA**: パラメータチューニングの進捗と結果
- **Instances**: インスタンス状態の更新

**実装参照**:
- Tournament API: `src/shogiarena/web/dashboard/backend/tournament/api.py`
- SPSA API: `src/shogiarena/web/dashboard/backend/spsa/api.py`
- SPSA Streams: `src/shogiarena/web/dashboard/backend/spsa/streams.py`

## Tournament SSE

### Endpoints

- **`/api/tournament/summary/stream`** - Tournament サマリーストリーム

### Design Goals

- **接続数削減**: Summary SSE は必要なタブでのみ開く
- **Live View との分離**: Live View は別の WebSocket パイプライン（`summary:update`）を使用
- **スナップショット保持**: SSE を閉じても最後の snapshot は破棄しない

### Summary SSE Activation

Summary SSE は以下のタブでのみアクティブ：

- `tournament`
- `openings`
- `engines`
- `rules`
- `games`

Summary SSE is stopped on other tabs (`live`, `instances`, `spsa`).

### Event Format

```
event: standings_update
data: {"standings": [...], "progress": {...}}
```

**実装参照**:
- Frontend subscriber: `frontend/src/modules/tournament/services/data.ts`

## SPSA SSE

### Design Philosophy

- **接続数制限**: ブラウザの EventSource 接続数上限を考慮（目安 6本）
- **必要なストリームのみ**: タブ別に必要なストリームだけを開く
- **集約ストリーム優先**: 解析系は集約ストリームで同期更新と接続数削減を両立
- **段階移行**: 既存ストリームを廃止する場合は REST との互換を維持

### Endpoints

#### Summary & Updates

- **`/api/spsa/summary/stream`** - SPSAサマリー
- **`/api/spsa/updates/stream`** - 更新イベント
- **`/api/spsa/update/detail/stream`** - 更新詳細（slim ビュー、`?view=slim&include=params,gradients`）

#### Analysis

- **`/api/spsa/analysis/correlation/stream`** - パラメータ相関
- **`/api/spsa/analysis/convergence`** - 収束分析（集約）

#### Variant & LTC

- **`/api/spsa/variant/games/stream`** - Variant 対局
- **`/api/spsa/ltc/games/stream`** - LTC 対局
- **`/api/spsa/ltc/results/stream`** - LTC 結果
- **`/api/spsa/ltc/progress/stream`** - LTC 進捗

### Convergence 集約 SSE

Convergence タブに表示する内容は `spsa_convergence` SSE で集約配信します。

```
event: convergence_update
data: {
  "ltc_results": {...},     // LTC 回帰結果（summary + results）
  "mobility_series": {...}  // Mobility 用の系列（gain_ak, variant_indices）
}
```

**目的**:
- Convergence タブの更新を **単一 SSE** で同期
- LTC Elo / Mobility / Δ-norm を同一タイミングで更新

**互換性**: `/api/spsa/ltc/results/stream` は互換用途で残すが、Convergence タブの描画は集約 SSE を優先します。

### Event Format

各 SSE エンドポイントの event 名 / payload 形は実装を source of truth とします。

**実装参照**:
- Frontend subscriber: `frontend/src/modules/spsa/services/streams.ts`

## Instances SSE

### Endpoints

- **`/api/instances/stream`** - インスタンス状態ストリーム

### Design

- **初回**: 全インスタンスのスナップショット
- **以降**: `instances_delta` イベントを優先

**実装参照**:
- Frontend: `frontend/src/modules/instances/services/`

## SSE 設計原則

### 接続管理

1. **タブのアクティブ性に応じて開閉**
   - アクティブなタブのみ SSE を開く
   - 非表示タブでは SSE を切断

2. **自動再接続**
   - EventSource は自動再接続をサポート
   - 再接続後は最新スナップショットを取得

3. **接続数制限**
   - 同一オリジンの EventSource は接続数上限あり（目安 6本）
   - 必要最小限のストリームのみ開く

### 欠落検知とフォールバック

#### 欠落検知条件

- SSE スナップショットの `total` が `results.length` を上回り、要求 limit を満たせない
- SSE ストリームが非アクティブ/切断状態であり、必要データが無い

#### フォールバック規約

- **欠落検知時のみ REST を呼ぶ**
- 連続 REST はクールダウン（例: 120秒）を適用
- REST は「整合性回復のための最小限の補完」に限定
- SSE には `seq` と `id` を付与し、欠落検知と再接続時の追いつきを可能にする
- ストリーム再接続直後は短いウォームアップ猶予を設け、不要な REST を避ける

詳細は [Dashboard Protocols](protocols.md) の「SPSA: LTC 結果のフォールバック方針」を参照してください。

## Event Structure

### Standard Format

```
event: <event_type>
data: <JSON_payload>
id: <optional_event_id>

```

### Common Fields

- `event`: イベントタイプ
- `data`: JSON ペイロード
- `id`: イベントID（欠落検知用、任意）

### Sequence Numbers

SSE イベントには `seq` または `id` を付与することで、欠落検知と追いつきを可能にします。

```json
{
  "seq": 123,
  "timestamp": 1730000000000,
  "data": {...}
}
```

## 実装参照

### サーバサイド

- **Tournament**: `src/shogiarena/web/dashboard/backend/tournament/api.py`
- **SPSA**: 
  - API: `src/shogiarena/web/dashboard/backend/spsa/api.py`
  - Streams: `src/shogiarena/web/dashboard/backend/spsa/streams.py`
- **Instances**: `src/shogiarena/web/dashboard/backend/instances/api.py`

### クライアントサイド

- **Tournament**: `frontend/src/modules/tournament/services/data.ts`
- **SPSA**: `frontend/src/modules/spsa/services/streams.ts`
- **Instances**: `frontend/src/modules/instances/services/`

## 関連ドキュメント

- [Dashboard Protocols](protocols.md) - 通信方式の選定指針
- [Dashboard Architecture](architecture.md) - 全体アーキテクチャ
- [WebSocket Protocol](websocket-protocol.md) - WebSocket プロトコル仕様
