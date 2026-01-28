# Project Structure

このドキュメントでは、ShogiArena のディレクトリ構成、アーキテクチャ、命名規則について説明します。

## ディレクトリ構成

### ルートレベル

```text
ShogiArena/
├── src/shogiarena/          # メインソースコード
├── tests/                   # テストコード
├── docs/                    # ドキュメント (MkDocs)
├── configs/                 # 設定テンプレート
├── examples/                # サンプル設定
├── stubs/                   # 型スタブ
├── .sandbox/                # 開発用サンドボックス (非コミット)
├── _refs/                   # 参照実装 (非コミット)
├── pyproject.toml           # プロジェクト設定
├── Makefile                 # 開発タスク
├── README.md                # プロジェクト概要
└── DEVELOPMENT.md           # 開発ガイド
```

### `src/shogiarena/`: メインソースコード

```text
src/shogiarena/
├── arena/                   # トーナメント実行の中核
│   ├── configs/             # 設定モデル (Pydantic/dataclass)
│   ├── engines/             # USI エンジンインターフェース
│   ├── orchestrators/       # 対局オーケストレーション
│   ├── runners/             # トーナメント/SPSA/SPRT ランナー
│   ├── services/            # 独立したサービス (Rating, SPRT, Stats)
│   └── instances/           # リモートインスタンス管理
├── cli/                     # CLI エントリポイントとコマンド
├── db/                      # データベース層 (SQLite/SQLAlchemy)
├── records/                 # 棋譜・結果データモデル
├── utils/                   # 共通ユーティリティ
└── web/                     # ダッシュボード
    ├── api_server.py        # FastAPI バックエンド
    └── dashboard/
        ├── backend/         # Python API モジュール
        └── frontend/        # TypeScript フロントエンド (Vite)
```

## コアモジュールの詳細

### `arena/engines/`: USI エンジンインターフェース

USI プロトコルを実装し、エンジンとの通信を管理します。

```text
arena/engines/
├── usi_process.py           # 低レベル USI プロセス管理
├── usi_protocol.py          # USI プロトコルパーサー
├── usi_engine.py            # AsyncUsiEngine (非同期インターフェース)
├── sync_usi_engine.py       # SyncUsiEngine (同期ラッパー)
├── usi_think.py             # 思考リクエスト/結果
├── usi_types.py             # USI 型定義
├── usi_config.py            # エンジン設定モデル
└── engine_factory.py        # エンジンファクトリー
```

**主要クラス**:

- **`AsyncUsiEngine`**: 非同期 USI エンジンインターフェース。並行処理や高度な制御に使用。
- **`SyncUsiEngine`**: 同期ラッパー。シンプルなスクリプトやインタラクティブ使用に最適。
- **`UsiProcess`**: プロセス管理とプロトコル通信の低レベル実装。

### `arena/orchestrators/`: 対局オーケストレーション

複数対局の並列実行、スケジューリング、リソース管理を担当します。

```text
arena/orchestrators/
├── base_orchestrator.py     # オーケストレーター基底クラス
├── local_orchestrator.py    # ローカル実行オーケストレーター
├── remote_orchestrator.py   # リモート実行オーケストレーター
└── game_executor.py         # 個別対局の実行ロジック
```

**責務**:

- 対局のスケジューリング（並列度制御）
- エンジンインスタンスのプーリング
- 持ち時間管理と判定（Draw, Win by eval）
- 棋譜の記録とデータベース保存

### `arena/runners/`: トーナメントランナー

トーナメント全体の実行フローを制御します。

```text
arena/runners/
├── base_runner.py           # ランナー基底クラス
├── tournament_runner.py     # ラウンドロビントーナメント
├── sprt_runner.py           # SPRT テスト
├── spsa_runner.py           # SPSA パラメータチューニング
├── run_controller.py        # 実行制御ロジック
├── reschedule_loop.py       # 再スケジューリングループ
└── dashboard_manager.py     # ダッシュボード管理
```

**主要ランナー**:

- **`TournamentRunner`**: 総当たり戦またはスイス式トーナメント
- **`SprtRunner`**: 統計的仮説検定による早期停止
- **`SpsaRunner`**: 勾配ベースのパラメータ最適化

### `arena/services/`: 独立サービス

トーナメントロジックから独立した機能を提供します。

```text
arena/services/
├── rating/                  # レーティング計算
│   ├── elo.py               # Elo レーティング
│   └── glicko.py            # Glicko レーティング
├── sprt/                    # SPRT 統計検定
│   └── sprt_service.py
├── statistics/              # 統計計算
│   └── game_statistics.py
└── adjudication/            # 判定ロジック
    └── adjudicator.py
```

**サービス例**:

- **Rating**: Elo, Glicko レーティングの計算
- **SPRT**: Sequential Probability Ratio Test の実装
- **Statistics**: 勝率、信頼区間、統計的有意性の計算
- **Adjudication**: 千日手、持将棋、評価値による勝敗判定

### `arena/configs/`: 設定モデル

YAML 設定ファイルを Python オブジェクトにマッピングします。

```text
arena/configs/
├── tournament.py            # トーナメント設定
├── spsa.py                  # SPSA 設定
├── engine.py                # エンジン設定
└── instance.py              # インスタンス設定
```

Pydantic または dataclass を使用した型安全な設定管理。

### `cli/`: CLI コマンド

`shogiarena` コマンドのサブコマンド実装。

```text
cli/
├── main.py                  # CLI エントリポイント
└── commands/
    ├── run.py               # `shogiarena run` コマンド
    ├── config.py            # `shogiarena config` コマンド
    ├── dashboard.py         # `shogiarena dashboard` コマンド
    └── analyze.py           # `shogiarena analyze` コマンド
```

### `web/dashboard/`: ダッシュボード

リアルタイムでトーナメントを監視する Web ダッシュボード。

```text
web/dashboard/
├── backend/                 # Python API モジュール
│   ├── live_view.py         # Live タブ API
│   ├── tournament_view.py   # Tournament タブ API
│   ├── spsa_view.py         # SPSA タブ API
│   └── games_view.py        # Games タブ API
└── frontend/                # TypeScript フロントエンド
    ├── src/
    │   ├── pages/           # ページコンポーネント
    │   ├── components/      # 再利用可能なコンポーネント
    │   ├── api/             # API クライアント
    │   └── utils/           # ユーティリティ
    ├── public/              # 静的アセット
    └── vite.config.ts       # Vite 設定
```

**技術スタック**:

- **Backend**: FastAPI, Server-Sent Events (SSE)
- **Frontend**: TypeScript, Vite, Tailwind CSS
- **Charts**: Chart.js, Plotly.js

## テストディレクトリ

```text
tests/
├── unit/                    # 単体テスト
│   ├── arena/               # arena モジュールのテスト
│   └── test_*.py            # 個別機能のテスト
├── integration/             # 統合テスト
│   └── test_*.py
└── property/                # プロパティベーステスト
    └── test_*.py
```

**テストカテゴリ**:

- **Unit**: 個別関数/クラスの単体テスト（高速、モック使用）
- **Integration**: コンポーネント間の統合テスト（実際のエンジンプロセス使用）
- **Property**: Hypothesis を使用した性質ベーステスト

## 設定とサンプル

### `configs/`: 設定テンプレート

```text
configs/
└── resources/
    ├── engines/             # エンジン設定テンプレート
    │   └── overlays/        # エンジンオーバーレイ
    └── instances/           # インスタンス設定テンプレート
```

### `examples/`: サンプル設定

```text
examples/
├── configs/
│   ├── run/
│   │   ├── tournament/      # トーナメント設定例
│   │   ├── sprt/            # SPRT 設定例
│   │   └── spsa/            # SPSA 設定例
│   └── resources/
│       ├── engines/         # エンジン設定例
│       ├── evals/           # 評価関数設定例
│       └── instances/       # インスタンス設定例
└── README.md                # サンプルの説明
```

## 開発専用ディレクトリ

### `.sandbox/`: 開発サンドボックス

開発時のテスト実行やデバッグに使用。Git では無視されます。

```text
.sandbox/
├── configs/                 # テスト用設定
│   ├── engine/
│   ├── run/
│   └── resources/
└── work_dir/                # 実行時出力
    ├── logs/
    ├── games/
    └── database.db
```

### `_refs/`: 参照実装

他プロジェクトの参考コード（読み取り専用）。Git では無視されます。

```text
_refs/
├── OpenBench/               # SPRT, 分散実行の参考
├── YaneuraOu/               # USI プロトコル実装の参考
├── cutechess/               # トーナメント管理の参考
├── fastshogi/               # 高速実行パターンの参考
└── cshogi/                  # Python 将棋ライブラリの参考
```

**用途**: アルゴリズムや実装パターンの学習・参照。

### `stubs/`: 型スタブ

外部ライブラリの型定義。

```text
stubs/
└── cshogi/                  # cshogi ライブラリの型スタブ
    ├── __init__.pyi
    ├── usi.pyi
    ├── KIF.pyi
    └── CSA.pyi
```

## 命名規則とコーディングスタイル

### Python コード

- **モジュール/パッケージ**: `snake_case`
- **クラス**: `PascalCase`
- **関数/変数**: `snake_case`
- **定数**: `UPPER_SNAKE_CASE`
- **プライベートメンバー**: `_leading_underscore`

**例**:
```python
class TournamentRunner:
    MAX_RETRIES = 3
    
    def __init__(self, config: ArenaConfig):
        self._config = config
    
    def run_sync(self) -> None:
        ...
```

### 設定ファイル

- **ファイル名**: `snake_case` (例: `tournament_config.yaml`)
- **YAML キー**: `snake_case`

**例**:
```yaml
experiment_name: "my_tournament"
tournament:
  scheduler: round_robin
  games_per_pair: 10
```

### TypeScript/JavaScript

- **変数/関数**: `camelCase`
- **クラス**: `PascalCase`
- **定数**: `UPPER_SNAKE_CASE`

**例**:
```typescript
class DashboardCore {
  private apiClient: ApiClient;
  
  async fetchTournamentData(): Promise<TournamentData> {
    ...
  }
}
```

## アーキテクチャ原則

### 設計思想

1. **非同期ファースト**: `asyncio` を使用した並行実行
2. **型安全**: mypy strict モードでの完全な型チェック
3. **モジュラー**: 明確な責務分離とインターフェース
4. **テスタブル**: 依存性注入とモック可能な設計
5. **観測可能**: 包括的なロギングとライブダッシュボード

### 依存関係の方向

```
CLI → Runners → Orchestrators → Engines
                     ↓
                  Services (Rating, SPRT, Stats)
                     ↓
                  Database
```

**ルール**:

- 上位層は下位層に依存できる
- 下位層は上位層に依存しない
- Services は独立した機能を提供（相互依存なし）

### エラーハンドリング

- **特定の例外をキャッチ**: `except Exception:` は避ける
- **適切なロギング**: エラー時は必ずログ出力
- **フェイルファースト**: フォールバックで隠蔽しない
- **破壊的変更 OK**: 後方互換性より clean code を優先

## ドキュメント構成

```text
docs/
├── getting-started/         # 初心者向けガイド
│   ├── installation.md
│   ├── quick-start.md
│   └── first-tournament.md
├── user-guide/              # 機能ガイド
│   ├── tournaments.md
│   ├── spsa.md
│   ├── python-library.md
│   └── dashboard.md
├── technical/               # 技術詳細
│   ├── architecture.md
│   ├── usi-engine.md
│   └── services.md
├── api/                     # API リファレンス
│   ├── core.md
│   ├── engines.md
│   └── services.md
└── development/             # 開発ガイド
    ├── contributing.md
    ├── project-structure.md
    └── testing.md
```

## ビルドとデプロイ

### パッケージ構造

```text
dist/
├── shogiarena-X.Y.Z.tar.gz       # ソース配布
└── shogiarena-X.Y.Z-py3-none-any.whl  # ホイール
```

### CI/CD パイプライン

1. **CI** (`.github/workflows/ci.yml`):
   - Linting (ruff)
   - Type checking (mypy)
   - Tests (pytest)
   - Frontend checks

2. **Docs** (`.github/workflows/docs.yml`):
   - MkDocs ビルド
   - GitHub Pages デプロイ

3. **Release** (`.github/workflows/release.yml`):
   - PyPI パブリッシュ
   - GitHub Release 作成

## 参考資料

- [DEVELOPMENT.md](https://github.com/nyoki-mtl/ShogiArena/blob/main/DEVELOPMENT.md): 開発ワークフローの詳細
- [Architecture](../technical/architecture.md): システムアーキテクチャの詳細
- [USI Engine](../technical/usi-engine.md): USI プロトコル実装の詳細
