# クイックスタート

このページでは、ローカルにある USI エンジン 2 つで最小構成のトーナメントを動かします。

## 1. エンジン設定を作る

`engine_a.yaml`:

```yaml
name: "EngineA"
engine_path: "/path/to/engine_a"
options:
  Threads: 2
  USI_Hash: 256
```

`engine_b.yaml`:

```yaml
name: "EngineB"
engine_path: "/path/to/engine_b"
options:
  Threads: 2
  USI_Hash: 256
```

`engine_path` には USI エンジンの実行ファイルを指定します。`options` は USI の `setoption` として送られます。

## 2. トーナメント設定を作る

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

現在のスケジューラは `round_robin` と `gauntlet` です。最初は `round_robin` が扱いやすいです。

## 3. 検証して実行する

```bash
shogiarena run tournament tournament.yaml --dry-run
shogiarena run tournament tournament.yaml
```

`dashboard.enabled: true` の場合は `http://localhost:8080` で実行状況を確認できます。

## 4. 結果を見る

`--run-dir` を指定しない場合、結果は標準出力先の run ディレクトリに保存されます。

```text
{output_dir}/runs/<experiment>-<hash8>/YYYYMMDDHHMMSS/
├── game.db
├── manifest.json
├── state.json
├── data/
├── records/
└── transcripts/
```

保存済み run のダッシュボード:

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

結果集計:

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format json
```

## サンプル設定

リポジトリには用途別のテンプレートがあります。

```bash
cp examples/configs/run/tournament/example.yaml tournament.yaml
cp examples/configs/run/sprt/example.yaml sprt.yaml
cp examples/configs/run/spsa/example.yaml spsa.yaml
```

サンプルには artifact 参照やプレースホルダーが含まれる場合があります。実行前に自分の環境に合わせて `engines`、`instances`、評価関数や定跡のパスを調整してから `--dry-run` で確認してください。

## 次のステップ

- [最初のトーナメント](first-tournament.md)
- [トーナメント](../user-guide/tournaments.md)
- [エンジン設定](../user-guide/engine-configuration.md)
- [ダッシュボード](../user-guide/dashboard.md)
