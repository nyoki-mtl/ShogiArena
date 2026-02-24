# ダッシュボードガイド

Shogi Arena のダッシュボードは、トーナメントや SPSA チューニングの進行状況をリアルタイムで可視化します。

## 起動方法

### トーナメント実行時
`TournamentRunConfig` で `dashboard.enabled: true` に設定すると自動的に起動します（デフォルトポート: 8080）。

```bash
shogiarena run tournament my_config.yaml
# -> http://localhost:8080
```

### スタンドアロン起動
保存済みのデータベース（`game.db`）を指定して起動することも可能です。

```bash
shogiarena dashboard serve --run-dir work_dir/... --port 8080
```

## ランタイムモード

ダッシュボードは実行内容に応じて自動的にモードが切り替わり、表示されるタブが変化します。

| モード | 用途 | 主なタブ |
|--------|------|---------|
| **tournament** | 複数エンジンのトーナメント | Tournament, Games, Engines, Live View, Instances |
| **spsa** | SPSA パラメータ最適化 | SPSA, Games, Live View, Instances |
| **match** | 2 エンジン間の対戦分析 | Match, Games, Live View |
| **sprt** | SPRT 統計テスト | SPRT, Games, Live View |
| **generate** | 棋譜生成 | Generate |

## 主要なビュー

### Tournament Dashboard
トーナメントの全体状況を表示します。

- **Overview**: 進行状況、エンジンの勝率ランキング。
- **Standings**: 順位表・レーティング推移。
- **Matchups**: エンジン間のヘッド・ツー・ヘッド成績。
- **Openings**: 戦型別の統計。
- **Engines**: 各エンジンのオプション設定とメトリクス。
- **Games**: 全対局のリスト。フィルタリングや棋譜ダウンロードが可能。
- **Rules**: 対局ルール（持ち時間、引き分け条件等）の表示。
- **Live View**: 現在実行中の対局をリアルタイムで盤面表示します。

### Match Dashboard
2 エンジン間の対戦専用ビューです。

- **Summary**: 勝率、Elo 推定値、信頼区間。
- **Timeline**: 対局結果の時系列推移。

### SPRT Dashboard
SPRT（Sequential Probability Ratio Test）統計テスト用のビューです。

- **Summary**: LLR（Log-Likelihood Ratio）、判定状態（H0/H1/未確定）。
- **Timeline**: LLR の推移グラフと信頼区間。

### SPSA Dashboard
チューニングセッション専用のビューです。

- **Summary**: チューニングの進捗と推定残り時間。
- **Parameters**: チューニング対象パラメータの現在値と変動履歴。
- **Updates**: 各更新ステップの詳細（勾配推定、バリアント結果）。
- **Correlation**: パラメータ間の相関分析。
- **Convergence**: 収束分析（LTC Elo、Mobility、Δ-norm）。

### Generate Dashboard
`run generate` 向けに、生成状況の集計を表示します（DB なしで run_dir のメタデータを参照）。

- **Generate Summary**: 生成ファイル数、総対局数、総局面数、総バイト数。

`run generate` を起動した場合は自動的に Generate タブが有効になり、スタンドアロン起動でも `experiment_name: generate` を含む run ディレクトリを指定すると Generate プロファイルが選択されます。

### Instances タブ
エンジンインスタンスのライフサイクル管理を行います。

- インスタンスの作成・停止・削除。
- 各インスタンスの健全性メトリクス。
- インスタンスごとの対局履歴。

### Scheduler タブ
マッチスケジュールの管理を行います（Runner が有効な場合のみ）。

- 対局のキャンセル・復元。
- インスタンスへの手動アサイン。
- リスケジュール。

## リアルタイム更新

ダッシュボードは WebSocket と Server-Sent Events (SSE) を使用してリアルタイム更新を行います。

- **Live View**: WebSocket（`/ws`）を使用し、盤面・時計・指し手を秒単位で更新。
- **Tournament/SPSA**: SSE でサマリー・順位表・パラメータを自動更新。
- **Instances**: SSE でインスタンス状態を監視。

ブラウザをリロードすることなく、最新の対局結果やレーティングが自動的に反映されます。

## エクスポート

- **棋譜**: 各対局の「Download」ボタン、または一括ダウンロード機能で SFEN/KIF/CSA 形式を取得可能。
- **データ**: CSV 形式での統計データエクスポートに対応しています。
