# Summary

- [ShogiArena](index.md)

---

# はじめに

- [インストール](getting-started/installation.md)
- [クイックスタート](getting-started/quick-start.md)
- [最初のトーナメント](getting-started/first-tournament.md)

---

# ユーザーガイド

- [トーナメント](user-guide/tournaments.md)
- [SPSA チューニング](user-guide/spsa.md)
- [ダッシュボード](user-guide/dashboard.md)
- [Python ライブラリ](user-guide/python-library.md)
- [設定]()
  - [エンジン設定](user-guide/engine-configuration.md)
  - [設定システム](user-guide/configuration.md)
  - [ビルドシステム](user-guide/build-system.md)
  - [定跡ファイル](user-guide/opening-books.md)
- [応用]()
  - [リモート実行](user-guide/remote-execution.md)
  - [ユーティリティ](user-guide/tools.md)

---

# 技術詳細

- [アーキテクチャ](technical/architecture.md)
- [Runner と Orchestrator](technical/runners-orchestrators.md)
- [サービスとユーティリティ](technical/services.md)
- [インスタンス管理](technical/instances.md)
- [エンジンレイヤー](technical/engine-layers.md)
- [USI エンジン設計](technical/usi-engine.md)
- [ダッシュボード内部]()
  - [アーキテクチャ](technical/dashboard/architecture.md)
  - [プロトコル](technical/dashboard/protocols.md)
  - [WebSocket プロトコル](technical/dashboard/websocket-protocol.md)
  - [SSE プロトコル](technical/dashboard/sse-protocol.md)
  - [スタイル](technical/dashboard/styles.md)
  - [Live API](technical/dashboard/live_api.md)
  - [テスト](technical/dashboard/testing.md)
  - [型定義](technical/dashboard/typing.md)

---

# 内部技術

- [統計的検定とチューニング](internals/index.md)
  - [Elo レーティング](internals/elo/index.md)
    - [BayesElo と引き分けモデル](internals/elo/bayeselo.md)
    - [正規化 Elo（nElo）](internals/elo/nelo.md)
  - [SPRT（逐次確率比検定）](internals/sprt/index.md)
    - [対数尤度比（LLR）](internals/sprt/llr.md)
    - [GSPRT（一般化 SPRT）](internals/sprt/gsprt.md)
    - [五項分布モデル](internals/sprt/pentanomial.md)
  - [SPSA（同時摂動確率近似法）](internals/spsa/index.md)
    - [勾配推定と摂動](internals/spsa/gradient.md)
    - [ゲインスケジュール](internals/spsa/gain-schedule.md)
    - [ノイズと高次手法の限界](internals/spsa/noise-and-higher-order.md)
    - [LTC 回帰テスト](internals/spsa/ltc-regression.md)
  - [分散削減テクニック](internals/variance-reduction/index.md)
  - [用語集](internals/glossary.md)
  - [テストフレームワーク概説](internals/testing-frameworks.md)

---

# API リファレンス

- [概要](api/index.md)
  - [CLI](api/cli.md)
  - [Configs](api/configs.md)
  - [Engines](api/engines.md)
  - [Runners](api/runners.md)
  - [Orchestrators](api/orchestrators.md)
  - [Scheduler](api/scheduler.md)
  - [Execution](api/execution.md)
  - [Services](api/services.md)
  - [Records](api/records.md)
  - [Instances](api/instances.md)
  - [Session](api/session.md)
  - [Storage](api/storage.md)
  - [Dashboard](api/dashboard.md)
  - [DB](api/db.md)
  - [Utils](api/utils.md)

---

# 開発

- [プロジェクト構成](development/project-structure.md)
- [コントリビュート](development/contributing.md)
- [Live Updates リファクタリング](development/live-updates-refactor.md)
- [フロントエンド Visual Testing](development/frontend-visual-testing.md)

---

- [トラブルシューティング](troubleshooting.md)
