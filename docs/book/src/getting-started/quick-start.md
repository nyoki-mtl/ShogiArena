# クイックスタート

このガイドでは、インストール直後に最初のトーナメントを 1 本動かすところまでを説明します。

## 前提条件

- Python 3.11 以上
- USI 対応の将棋エンジン 2 つ以上

インストール自体は [インストール](installation.md) を参照してください。

## 1. 環境の初期化

標準の `output_dir` と `engine_dir` を使いたい場合は最初に設定します。

```bash
shogiarena config init
```

`config init` をしない場合でも動作はします。このときは次のデフォルト値が使われます。

- `output_dir`: `./shogiarena_output`
- `engine_dir`: システム一時ディレクトリ配下の `shogiarena-engines`

設定内容の確認:

```bash
shogiarena config show
```

## 2. エンジン設定ファイルの作成

ShogiArena のエンジン YAML では `path` ではなく `engine_path` を使います。

`engine_a.yaml`:

```yaml
name: "EngineA"
engine_path: "/path/to/engine_a"
options:
  Threads: 2
  Hash: 256
```

`engine_b.yaml`:

```yaml
name: "EngineB"
engine_path: "/path/to/engine_b"
options:
  Threads: 2
  Hash: 256
```

`config init` 済みなら `{engine_dir}` プレースホルダーも使えます。

```yaml
name: "YaneuraOu"
engine_path: "{engine_dir}/yaneuraou/YaneuraOu"
options:
  Threads: 4
  Hash: 1024
```

## 3. トーナメント設定ファイルの作成

`tournament.yaml`:

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
    time_ms: 10000
    increment_ms: 100

dashboard:
  enabled: true
  api_port: 8080
```

## 4. 実行

まずは設定だけ検証したいなら:

```bash
shogiarena run tournament tournament.yaml --dry-run
```

問題なければそのまま実行します。

```bash
shogiarena run tournament tournament.yaml
```

`dashboard.enabled: true` の場合は `http://localhost:8080` にダッシュボードが立ち上がります。

## 5. 結果の保存先

`--run-dir` を指定しない場合、結果は次のようなディレクトリに保存されます。

```text
{output_dir}/
├── logs/                          # グローバルログディレクトリ
└── runs/<config名またはexperiment_name>-<hash8>/YYYYMMDDHHMMSS/
    ├── game.db
    ├── state.json
    ├── manifest.json
    ├── data/
    └── records/
```

`--run-dir` を指定した場合は、そのパスがそのまま run ディレクトリになります。

## 6. 過去 run のダッシュボード表示

```bash
# 設定ファイルから最新 run を解決
shogiarena dashboard serve --config tournament.yaml

# または run ディレクトリを直接指定
shogiarena dashboard serve --run-dir /path/to/run
```

## 次のステップ

- [初めてのトーナメント](first-tournament.md)
- [トーナメントの実行](../user-guide/tournaments.md)
- [エンジン設定ファイル](../user-guide/engine-configuration.md)
- [設定システム](../user-guide/configuration.md)
- [リモート実行](../user-guide/remote-execution.md)
