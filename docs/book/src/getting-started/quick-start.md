# クイックスタート

このガイドでは、ShogiArena のインストールから最初のトーナメント実行までを説明します。

## 前提条件

- Python 3.10 以上
- USI プロトコル対応の将棋エンジン

インストール方法は [インストールガイド](installation.md) を参照してください。

## 1. 環境の初期化

インストール後、最初に環境設定を行います：

```bash
shogiarena init
```

このコマンドは対話的に以下の項目を設定します：

- **出力ディレクトリ**: トーナメント結果の保存先
- **エンジンディレクトリ**: エンジンバイナリのキャッシュ先
- **リポジトリ登録**: YaneuraOu などのエンジンリポジトリの登録（オプション）

### デフォルト設定

`shogiarena init` を実行しない場合、以下のデフォルト値が使用されます：

- **出力ディレクトリ**: `./shogiarena_output`（カレントディレクトリのサブディレクトリ）
- **エンジンディレクトリ**: 一時ディレクトリ（例: Linux では `/tmp/shogiarena-engines`）

### プラットフォーム別のデフォルトパス

`shogiarena init` で設定した場合のデフォルトパス：

- **Linux**: `~/.local/share/shogiarena/output`
- **Windows**: `%LOCALAPPDATA%\shogiarena\output`
- **macOS**: `~/Library/Application Support/shogiarena/output`

設定は `~/.config/shogiarena/settings.yaml`（Linux の場合）に保存されます。

### 非対話的な初期化

CI や自動化スクリプトでは、`--non-interactive` オプションを使用できます：

```bash
shogiarena config init --non-interactive \
  --output-dir /path/to/output \
  --engine-dir /path/to/engines
```

### プレースホルダー

設定ファイル内で以下のプレースホルダーが使用できます：

- `{output_dir}`: 設定した出力ディレクトリ
- `{engine_dir}`: 設定したエンジンディレクトリ

例：
```yaml
path: "{engine_dir}/yaneuraou/YaneuraOu"
```

### 設定の確認と更新

```bash
# 現在の設定を表示
shogiarena config show

# リポジトリを追加
shogiarena config repo set yaneuraou \
  --path ~/repos/YaneuraOu \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml

# リポジトリを削除
shogiarena config repo remove yaneuraou
```

## 2. エンジン設定ファイルの準備

対局させるエンジンの設定ファイルを作成します。

**engine_a.yaml**:
```yaml
name: "EngineA"
path: "/path/to/your/engine_a"
options:
  Threads: 4
  Hash: 256
```

**engine_b.yaml**:
```yaml
name: "EngineB"
path: "/path/to/your/engine_b"
options:
  Threads: 4
  Hash: 256
```

プレースホルダーを使用する場合：
```yaml
name: "YaneuraOu"
path: "{engine_dir}/yaneuraou/YaneuraOu"
options:
  Threads: 8
  Hash: 1024
```

> **Tip: エンジンがない場合**
>
> テスト用のダミーエンジンやサンプル設定については [初めてのトーナメント](first-tournament.md) を参照してください。


## 3. トーナメント設定ファイルの作成

**tournament.yaml**:
```yaml
experiment_name: "my_first_tournament"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 10
  num_parallel: 2

rules:
  time_control:
    time_ms: 10000      # 10秒
    increment_ms: 100   # +0.1秒
  
dashboard:
  enabled: true
  port: 8080
```

## 4. トーナメントの実行

```bash
shogiarena run tournament tournament.yaml
```

コマンド実行後、以下が自動的に開始されます：

- エンジンプロセスの起動
- 対局の実行
- ダッシュボードサーバー（`http://localhost:8080`）

実行ログには対局の進行状況がリアルタイムで表示されます。

## 5. 結果の確認

### ダッシュボード

ブラウザで `http://localhost:8080` にアクセスすると、以下の情報を確認できます：

- **Live タブ**: リアルタイムの対局状況
- **Tournament タブ**: 総合成績とレーティング
- **Games タブ**: 個別対局の棋譜と詳細
- **Engines タブ**: エンジン情報とパフォーマンス

### 出力ディレクトリ

トーナメント結果は以下の形式で保存されます：

```
{output_dir}/tournament/<config名>-<hash>/YYYYMMDDHHMMSS/
├── games/          # 棋譜ファイル（KIF, CSA, PSV, Sbinpack）
├── logs/           # 実行ログ
├── game.db         # 対局結果データベース
└── config.yaml     # 使用した設定のコピー
```

### 完了後のダッシュボード表示

トーナメント完了後も、過去の結果をダッシュボードで確認できます：

```bash
# 設定ファイルから実行ディレクトリを解決
shogiarena dashboard serve --config tournament.yaml

# または実行ディレクトリを直接指定
shogiarena dashboard serve --run-dir /path/to/tournament/run
```

## 次のステップ

基本的な使い方を理解したら、以下のドキュメントで詳細を学びましょう：

### チュートリアル

- **[初めてのトーナメント](first-tournament.md)** - より詳しいチュートリアル

### 設定ガイド

- **[エンジン設定](../user-guide/engine-configuration.md)** - エンジン設定ファイルの詳細
- **[設定システム](../user-guide/configuration.md)** - 環境設定の詳細
- **[開局集の管理](../user-guide/opening-books.md)** - 初期局面の設定方法

### 使い方ガイド

- **[トーナメントガイド](../user-guide/tournaments.md)** - 高度な設定オプション
- **[SPSA チューニング](../user-guide/spsa.md)** - パラメータ最適化
- **[Python ライブラリ](../user-guide/python-library.md)** - プログラムからの使用方法
- **[リモート実行](../user-guide/remote-execution.md)** - SSH 経由の分散実行

### その他

- **[トラブルシューティング](../troubleshooting.md)** - よくある問題と解決方法
