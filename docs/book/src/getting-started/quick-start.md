# クイックスタート

ローカルにある USI エンジン 2 つで、最小構成のトーナメントを動かします。
インストールがまだなら[インストール](installation.md)を先に済ませてください。

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

`engine_path` には USI エンジンの実行ファイルを指定します。
`options` は USI の `setoption` として送られます。

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

利用できるスケジューラは `round_robin` と `gauntlet` です。
全エンジンを総当たりさせる `round_robin` が、最初の一回には扱いやすいです。

## 3. 検証して実行する

```bash
shogiarena run tournament tournament.yaml --dry-run
shogiarena run tournament tournament.yaml
```

`dashboard.enabled: true` の場合は `http://localhost:8080` で実行状況を確認できます。

## 4. 結果を見る

`--run-dir` を指定しない場合、結果は[初期設定](installation.md)で決めた出力先の下の run ディレクトリに保存されます。

```text
{output_dir}/tournament/runs/<experiment>-<hash8>/YYYYMMDDHHMMSS/
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

サンプルには artifact 参照やプレースホルダーが含まれる場合があります。
`engines`、`instances`、評価関数や定跡のパスを自分の環境に合わせてから、`--dry-run` で確認してください。

## 次のステップ

- [最初のトーナメント](first-tournament.md)：同じエンジンの設定違いを比較する
- [トーナメント](../user-guide/tournaments.md)：スケジューラと並列数の詳細
- [エンジン設定](../user-guide/engine-configuration.md)：エンジン YAML の全項目
- [ダッシュボード](../user-guide/dashboard.md)：画面ごとの見方
