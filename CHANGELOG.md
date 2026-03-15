# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0]

*v0.1.x からの大規模リファクタリングにより、すべてのモジュール構成・API・設定形式が刷新されました。
以前のバージョンとの後方互換性はありません。*

### Changed
- **Architecture**: Bounded context パターンによる `_core/` 4層構造 (contexts, interfaces, platform, shared) へ全面移行
- **Public API**: `shogiarena.engine`, `shogiarena.tournament`, `shogiarena.cli`, `shogiarena.composition` を正式な公開面として整理
- **CLI**: `shogiarena config init` / `run tournament` / `run sprt` / `run spsa` / `dashboard serve` 等のサブコマンド体系に再編
- **Dashboard**: WebSocket/SSE ベースのリアルタイム更新、モジュール分割されたフロントエンド (vanilla TypeScript + Vite)
- **Engine runtime**: USI プロトコル実装の刷新、ポンダー・詰将棋探索ステートの追加
- **Tournament**: ラウンドロビン・ガントレット・セルフプレイ方式のスケジューラ
- **SPSA**: ゲインスケジュール、LTC 回帰テスト、分散削減テクニックの統合
- **Rating**: Bradley-Terry-Davidson モデルによる Elo 推定、五項分布 SPRT
- **Config**: Pydantic ベースの型安全な設定システム、artifact ビルド・リモート実行対応
- **Documentation**: mdBook ベースの包括的ドキュメント整備

[Unreleased]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.3.0
