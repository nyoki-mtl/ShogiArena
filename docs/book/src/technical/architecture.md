# アーキテクチャ概要

Shogi Arena は、拡張性と堅牢性を重視した階層型アーキテクチャを採用しています。

## レイヤー構造

システムは以下の 4 つの主要なレイヤーで構成されています。

### 1. User Interface Layer (CLI & Dashboard)
ユーザーとの接点です。
- **CLI**: `argparse` ベースの統一コマンドインターフェース。
- **Dashboard**: `FastAPI` (Backend) と `Vanilla TS` (Frontend) によるリアルタイム監視。

### 2. Management Layer (Runners & Orchestrators)
実行のフローとリソースを管理します。
- **Runner**: 実行のライフサイクル（開始、集計、終了）を管理。
    - `TournamentRunner`: 定常的な対局。
    - `SpsaRunner`: パラメータ自動チューニング。
    - `SprtRunner`: 逐次確率比検定。
- **Orchestrator**: `asyncio` を駆使した並列実行とインスタンス（Local/SSH）への割り当て。

### 3. Execution Layer (GameRunner & Engine Layer)
実際の対局を 1 局単位で実行します。
- **GameRunner**: 2 つのエンジンを対戦させ、ルールに基づいた勝敗判定を行います。
- **Engine Layer**: USI プロトコルを介したエンジンとの低レベル通信。
  - 6つの明確なレイヤー（Bridge → Process → Engine → Sync Wrapper → Factory）で構成
  - ローカル/SSH実行の抽象化、プロセス管理、セッション制御を担当
  - 詳細は [エンジンラッパーのレイヤー設計](engine-layers.md) を参照

### 4. Data & Service Layer
永続化とビジネスロジックを提供します。
- **Services**: Rating (Elo), SPRT, Adjudication (判定)。
- **Persistence**: `SQLAlchemy` を使用した対局結果とレーティング履歴の保存。

## 主要なデータフロー

1. **初期化**: CLI が YAML を読み込み `TournamentRunConfig` を生成。
2. **準備**: `EngineFactory` が必要なバイナリのビルドや配布（SSH）を実施。
3. **実行**: `Runner` がスケジュールを生成し、`Orchestrator` を通じて `GameRunner` を並列起動。
4. **フィードバック**: 各手の評価値や進行状況が `SSE` を通じてダッシュボードに送信され、結果が DB に保存される。

## 非同期処理のデザイン

Shogi Arena は、多数のエンジンプロセスを効率的に管理するために `asyncio` を全面的に採用しています。
- エンジンの入出力監視（stdout/stdin）をノンブロッキングで実施。
- SSH 経由のリモート実行も `asyncssh` により統合。
- 同時対局数はセマフォによって制御され、CPU リソースを最適に配分します。
