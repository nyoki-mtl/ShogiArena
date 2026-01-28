# Dashboard Testing

ダッシュボードのテスト運用ルールと補助ユーティリティの整理用ページです。

## 基本方針

- テストは機能単位で `modules/<feature>/__tests__` に配置する。
- 共有テストヘルパーは `modules/<feature>/testing` に集約する。
- ブート順序など横断的な検証は `bootstrap.*.test.ts` に置く。

## 参照先

- モジュール構成は `architecture.md` を参照。
- 型設計は `typing.md` を参照。
