# Dashboard Guide

Shogi Arena のダッシュボードは、トーナメントや SPSA チューニングの進行状況をリアルタイムで可視化します。

## 起動方法

### トーナメント実行時
`ArenaConfig` で `dashboard.enabled: true` に設定すると自動的に起動します（デフォルトポート: 8080）。

```bash
shogiarena run tournament my_config.yaml
# -> http://localhost:8080
```

### スタンドアロン起動
保存済みのデータベース（`game.db`）を指定して起動することも可能です。

```bash
shogiarena dashboard serve --run-dir work_dir/... --port 8080
```

## 主要なビュー

### Tournament Dashboard
トーナメントの全体状況を表示します。

- **Overview**: 進行状況、エンジンの勝率ランキング。
- **Engines**: 各エンジンのレーティング推移グラフ。
- **Games**: 全対局のリスト。フィルタリングや棋譜ダウンロードが可能。
- **Live View**: 現在実行中の対局をリアルタイムで盤面表示します。

### SPSA Dashboard
チューニングセッション専用のビューです。

- **Progress**: チューニングの進捗と推定残り時間。
- **Parameters**: チューニング対象パラメータの現在値と変動履歴。
- **Gradients**: 各ステップでの勾配推定の様子。

## リアルタイム更新

ダッシュボードは Server-Sent Events (SSE) を使用してブラウザへプッシュ通知を行います。リロードすることなく最新の対局結果やレーティングが反映されます。

## エクスポート

- **棋譜**: 各対局の「Download」ボタン、または一括ダウンロード機能で SFEN/KIF/CSA 形式を取得可能。
- **データ**: CSV 形式での統計データエクスポートに対応しています。
