# Project Structure

このドキュメントでは、ShogiArena のディレクトリ構成、アーキテクチャ、命名規則について説明します。

## ディレクトリ構成

### ルートレベル

```text
ShogiArena/
├── src/shogiarena/          # メインソースコード
├── tests/                   # テストコード
├── docs/                    # ドキュメント (mdBook)
├── configs/                 # 設定テンプレート
├── examples/                # サンプル設定
├── stubs/                   # 型スタブ (Windows 向け補助)
├── pyproject.toml           # プロジェクト設定
├── Makefile                 # 開発タスク
└── README.md                # プロジェクト概要
```

### `src/shogiarena/`: メインソースコード

```text
src/shogiarena/
├── arena/                   # トーナメント実行の中核
│   ├── configs/             # 設定モデル (dataclass)
│   ├── engines/             # USI エンジンインターフェース
│   ├── execution/           # 個別対局の実行ロジック
│   ├── instances/           # リモートインスタンス管理・プーリング
│   ├── orchestrators/       # 対局オーケストレーション
│   ├── remote/              # SSH リモート実行
│   ├── results/             # 実行結果モデル
│   ├── runners/             # トーナメント/SPRT/SPSA ランナー
│   ├── scheduler/           # 対局スケジューリング
│   ├── services/            # 独立サービス (Rating, SPRT, Stats, DB)
│   ├── session/             # セッション管理
│   ├── storage/             # ファイルシステムベースの実行ストレージ
│   └── tuning/              # SPSA チューニングロジック
├── cli/                     # CLI エントリポイントとコマンド
├── db/                      # データベース層 (SQLite/SQLAlchemy)
├── records/                 # 棋譜フォーマットとストレージ
├── utils/                   # 共通ユーティリティ
└── web/                     # ダッシュボード (本ドキュメントの範囲外)
```

## コアモジュールの詳細

### `arena/engines/`: USI エンジンインターフェース

USI プロトコルを実装し、エンジンとの通信を管理します。

```text
arena/engines/
├── usi_process.py           # 低レベル USI プロセス管理
├── usi_protocol.py          # USI プロトコルパーサー
├── usi_bridge.py            # USI ブリッジ (プロセス通信の抽象化)
├── usi_bridge_spawner.py    # ブリッジスポナー
├── usi_engine.py            # AsyncUsiEngine (非同期インターフェース)
├── sync_usi_engine.py       # SyncUsiEngine (同期ラッパー)
├── usi_think.py             # 思考リクエスト/結果
├── usi_types.py             # USI 型定義
├── usi_config.py            # エンジン設定モデル
├── usi_compliance.py        # USI 準拠チェック
├── time_control.py          # 持ち時間管理
└── engine_factory.py        # エンジンファクトリー
```

**主要クラス**:

- **`AsyncUsiEngine`**: 非同期 USI エンジンインターフェース。並行処理や高度な制御に使用。
- **`SyncUsiEngine`**: 同期ラッパー。シンプルなスクリプトやインタラクティブ使用に最適。
- **`UsiProcess`**: プロセス管理とプロトコル通信の低レベル実装。
- **`UsiBridge`**: プロセス通信の抽象化レイヤー。

### `arena/execution/`: 対局実行

個別の対局実行ロジックを管理します。

```text
arena/execution/
├── game_runner.py           # 対局実行エンジン
├── engine_participant.py    # 対局参加者としてのエンジン
└── types.py                 # 実行関連の型定義
```

### `arena/orchestrators/`: 対局オーケストレーション

複数対局の並列実行、スケジューリング、リソース管理を担当します。

```text
arena/orchestrators/
├── base_orchestrator.py     # オーケストレーター基底クラス
├── base_orchestrator_utils.py  # 共通ユーティリティ
├── tournament_orchestrator.py  # トーナメント用オーケストレーター
├── spsa_orchestrator.py     # SPSA 用オーケストレーター
├── engine_pool.py           # エンジンプーリング
├── progress.py              # 進捗管理
├── remote_exec.py           # リモート実行サポート
└── spsa/                    # SPSA 固有のオーケストレーションロジック
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
├── tournament_runner.py     # ラウンドロビン/スイス式トーナメント
├── sprt_runner.py           # SPRT テスト
├── spsa_runner.py           # SPSA パラメータチューニング
├── run_controller.py        # 実行制御ロジック
├── reschedule_loop.py       # 再スケジューリングループ
├── session_context_builder.py  # セッションコンテキスト構築
├── session_flow.py          # セッション実行フロー
├── results.py               # ランナー結果
├── reporting.py             # レポート生成
├── tqdm_reporter.py         # tqdm 進捗表示
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
├── persistence/             # データ永続化
│   ├── db_service.py        # データベースサービス
│   ├── result_store.py      # 結果ストア
│   └── records.py           # 棋譜記録
├── statistics/              # 統計計算
│   ├── rating_service.py    # レーティング計算
│   ├── sprt.py              # SPRT 統計検定
│   ├── pentanomial.py       # ペンタノミアルモデル
│   └── btd.py               # BTD 分析
├── game_control/            # 対局制御
│   └── adjudication.py      # 千日手・持将棋・評価値判定
├── artifacts/               # アーティファクトビルド
│   ├── builder.py           # ビルダー
│   └── resolver.py          # リゾルバー
└── openbench.py             # OpenBench 互換インターフェース
```

**主要サービス**:

- **Persistence**: データベース、結果保存、棋譜記録の永続化
- **Statistics**: Elo レーティング、SPRT 検定、ペンタノミアルモデルの計算
- **Game Control**: 千日手、持将棋、評価値による勝敗判定
- **Artifacts**: エンジンバイナリのビルドと解決

### `arena/configs/`: 設定モデル

YAML 設定ファイルを Python オブジェクトにマッピングします。

```text
arena/configs/
├── base.py                  # 基底設定クラス
├── tournament.py            # トーナメント設定
├── spsa.py                  # SPSA 設定
└── errors.py                # 設定エラー定義
```

dataclass を使用した型安全な設定管理。

### `arena/results/`: 実行結果モデル

```text
arena/results/
├── base.py                  # 基底結果クラス
├── tournament.py            # トーナメント結果
├── sprt.py                  # SPRT 結果
└── spsa.py                  # SPSA 結果
```

### `arena/storage/`: 実行ストレージ

```text
arena/storage/
└── run_storage.py           # ファイルシステムベースのストレージ (FilesystemRunStorage)
```

### その他の arena サブモジュール

```text
arena/instances/             # リモートインスタンス管理
├── pool.py                  # インスタンスプール
├── spawner.py               # インスタンス生成
├── store.py                 # インスタンス情報管理
├── models.py                # インスタンスモデル
├── health.py                # ヘルスチェック
├── provision.py             # プロビジョニング
├── remote_probe.py          # リモート検証
└── slot_policy.py           # スロットポリシー

arena/remote/                # SSH リモート実行
├── ssh_transport.py         # SSH トランスポート
├── executor.py              # リモート実行
└── repo_manager.py          # リポジトリ管理

arena/scheduler/             # 対局スケジューリング
└── game_scheduler.py        # スケジューラー (ラウンドロビン, スイス式, ガントレット)

arena/session/               # セッション管理
├── context.py               # セッションコンテキスト
├── state.py                 # セッション状態
└── hooks.py                 # セッションフック

arena/tuning/                # SPSA チューニング
├── spsa_tune.py             # SPSA アルゴリズム実装
└── param_io.py              # パラメータ入出力
```

### `cli/`: CLI コマンド

`shogiarena` コマンドのサブコマンド実装。

```text
cli/
├── __init__.py              # CLI エントリポイント (main)
└── commands/
    ├── _helpers.py          # 共通ヘルパー
    ├── config/              # `shogiarena config` - 環境設定
    │   └── _setup_utils.py
    ├── run/                 # `shogiarena run` - 実行コマンド群
    │   ├── tournament.py    # `shogiarena run tournament`
    │   ├── sprt.py          # `shogiarena run sprt`
    │   ├── spsa.py          # `shogiarena run spsa`
    │   ├── analyze.py       # `shogiarena run analyze`
    │   ├── generate.py      # `shogiarena run generate`
    │   ├── mate.py          # `shogiarena run mate`
    │   ├── base_run.py      # 共通実行ロジック
    │   ├── tournament_like.py  # Tournament/SPRT 共通ロジック
    │   ├── config_builder.py   # 設定構築
    │   ├── config_overrides.py # 設定オーバーライド
    │   └── helpers/         # 実行ヘルパー
    ├── dashboard/           # `shogiarena dashboard` - ダッシュボード
    └── internal/            # `shogiarena internal` - 内部コマンド
        └── remote.py        # リモート実行用
```

### `records/`: 棋譜フォーマット

棋譜のパース、フォーマット変換、ストレージを管理します。

```text
records/
├── formats/                 # 棋譜フォーマット
│   ├── base.py              # 基底フォーマットクラス
│   ├── registry.py          # フォーマットレジストリ
│   ├── kif.py               # KIF フォーマット
│   ├── csa.py               # CSA フォーマット
│   ├── psv.py               # PSV フォーマット
│   └── sbinpack.py          # Sbinpack バイナリフォーマット
└── storage/                 # 棋譜ストレージ
    ├── db_store.py          # データベースストア
    └── binary_writer.py     # バイナリファイルライター
```

### `db/`: データベース層

SQLAlchemy を使用した SQLite データベース管理。

```text
db/
├── models.py                # SQLAlchemy モデル定義
├── repository.py            # リポジトリパターン
└── factory.py               # DB セッションファクトリー
```

### `utils/`: 共通ユーティリティ

```text
utils/
├── common/                  # 汎用ユーティリティ
│   ├── settings.py          # アプリケーション設定
│   ├── project_dirs.py      # プロジェクトディレクトリ管理
│   ├── run_paths.py         # 実行パス管理
│   ├── paths.py             # パスユーティリティ
│   ├── logging.py           # ロギング設定
│   └── locks.py             # ロック管理
├── types/                   # 型定義
│   └── types.py             # 共通型定義
└── cpuinfo.py               # CPU 情報取得
```

## テストディレクトリ

```text
tests/
├── unit/                    # 単体テスト
│   ├── arena/               # arena モジュールのテスト
│   ├── cli/                 # CLI のテスト
│   ├── web/                 # Web のテスト
│   ├── utils/               # ユーティリティのテスト
│   └── test_*.py            # 個別機能のテスト
└── integration/             # 統合テスト
    ├── arena/               # arena 統合テスト
    └── live/                # ライブビュー統合テスト
```

**テストカテゴリ**:

- **Unit**: 個別関数/クラスの単体テスト（高速、モック使用）
- **Integration**: コンポーネント間の統合テスト（実際のエンジンプロセス使用）

## 設定とサンプル

### `configs/`: 設定テンプレート

```text
configs/
├── resources/
│   ├── engines/             # エンジン設定テンプレート
│   │   └── overlays/        # エンジンオーバーレイ
│   └── instances/           # インスタンス設定テンプレート
└── run/
    ├── arena/               # アリーナ実行設定
    ├── match/               # マッチ設定
    ├── sprt/                # SPRT 設定
    └── spsa/                # SPSA 設定
```

### `examples/`: サンプル設定

```text
examples/
└── configs/
    ├── arena_full_reference.yaml  # フルリファレンス設定
    ├── example.yaml               # 基本サンプル
    ├── run/
    │   ├── generate/        # 棋譜生成設定例
    │   ├── sprt/            # SPRT 設定例
    │   └── spsa/            # SPSA 設定例
    └── resources/
        ├── engines/         # エンジン設定例
        └── instances/       # インスタンス設定例
```

## 型スタブ

### `stubs/`: 補助型スタブ

Windows 環境向けの型スタブを提供します。

```text
stubs/
├── msvcrt.pyi               # Windows MSVC ランタイム
└── winreg.pyi               # Windows レジストリ
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

    def __init__(self, config: TournamentRunConfig):
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
2. **型安全**: ty による型チェック
3. **モジュラー**: 明確な責務分離とインターフェース
4. **テスタブル**: 依存性注入とモック可能な設計
5. **観測可能**: 包括的なロギングとライブダッシュボード

### 依存関係の方向

```
CLI → Runners → Orchestrators → Engines
                     ↓
      Services (Rating, SPRT, Stats, Persistence)
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
docs/book/src/
├── getting-started/         # 初心者向けガイド
│   ├── installation.md
│   ├── quick-start.md
│   └── first-tournament.md
├── user-guide/              # 機能ガイド
│   ├── tournaments.md
│   ├── spsa.md
│   ├── python-library.md
│   ├── dashboard.md
│   ├── engine-configuration.md
│   ├── configuration.md
│   ├── build-system.md
│   ├── opening-books.md
│   ├── remote-execution.md
│   └── tools.md
├── technical/               # 技術詳細
│   ├── architecture.md
│   ├── engine-layers.md
│   ├── usi-engine.md
│   └── services.md
├── api/                     # API リファレンス
│   └── index.md
└── development/             # 開発ガイド
    ├── contributing.md
    └── project-structure.md
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
   - Type checking (ty)
   - Tests (pytest)
   - Frontend checks

2. **Docs** (`.github/workflows/docs.yml`):
   - mdBook ビルド
   - GitHub Pages デプロイ

3. **Release** (`.github/workflows/release.yml`):
   - PyPI パブリッシュ
   - GitHub Release 作成

## 参考資料

- [Architecture](../technical/architecture.md): システムアーキテクチャの詳細
- [USI Engine](../technical/usi-engine.md): USI プロトコル実装の詳細
