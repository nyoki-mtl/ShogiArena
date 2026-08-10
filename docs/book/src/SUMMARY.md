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
- [SPRT / SPSA](user-guide/spsa.md)
- [ダッシュボード](user-guide/dashboard.md)
- [CSA 対局の観戦](user-guide/csa-watch.md)
- [エンジン設定](user-guide/engine-configuration.md)
- [設定システム](user-guide/configuration.md)
- [Python ライブラリ](user-guide/python-library.md)
- [リモート実行](user-guide/remote-execution.md)
- [ユーティリティ](user-guide/tools.md)

---

# リファレンス

- [公開 API](api/index.md)
- [CLI](api/cli.md)

---

# 内部技術

- [統計的検定とチューニング](internals/index.md)
  - [Elo レーティング](internals/elo/index.md)
    - [BayesElo と引き分けモデル](internals/elo/bayeselo.md)
    - [正規化 Elo（nElo）](internals/elo/nelo.md)
  - [SPRT](internals/sprt/index.md)
    - [対数尤度比（LLR）](internals/sprt/llr.md)
    - [GSPRT](internals/sprt/gsprt.md)
    - [五項分布モデル](internals/sprt/pentanomial.md)
  - [SPSA](internals/spsa/index.md)
    - [勾配推定と摂動](internals/spsa/gradient.md)
    - [ゲインスケジュール](internals/spsa/gain-schedule.md)
    - [ノイズと高次手法の限界](internals/spsa/noise-and-higher-order.md)
    - [LTC 回帰テスト](internals/spsa/ltc-regression.md)
  - [分散削減テクニック](internals/variance-reduction/index.md)
  - [テストフレームワーク概説](internals/testing-frameworks.md)
  - [用語集](internals/glossary.md)

---

# 開発

- [プロジェクト構成](development/project-structure.md)
- [コントリビュート](development/contributing.md)

---

# サポート

- [トラブルシューティング](troubleshooting.md)
