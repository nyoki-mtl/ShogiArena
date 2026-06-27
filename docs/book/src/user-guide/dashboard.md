# ダッシュボード

ShogiArena のダッシュボードは、実行中または保存済み run の状態をブラウザで確認するための UI です。

## 実行中に開く

run 設定で `dashboard.enabled: true` にすると、実行時に API サーバーが起動します。

```yaml
dashboard:
  enabled: true
  api_port: 8080
```

```bash
shogiarena run tournament tournament.yaml
```

ブラウザで `http://localhost:8080` を開きます。

## 保存済み run を開く

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

設定ファイルから最新 run を解決したい場合:

```bash
shogiarena dashboard serve --config tournament.yaml
```

ポートを変える場合:

```bash
shogiarena dashboard serve --run-dir /path/to/run --port 9090
```

## 表示内容

実行モードに応じて表示されるタブが切り替わります。

| モード | 主な用途 | 主な表示 |
| --- | --- | --- |
| `tournament` | 複数エンジン比較 | 順位表、対戦表、棋譜、エンジン設定 |
| `sprt` | 2 エンジンの統計検定 | LLR、判定状態、対局履歴 |
| `spsa` | パラメータチューニング | 更新履歴、パラメータ、収束状況 |
| `generate` | 自己対局の棋譜生成 | 生成数、出力ファイル、進捗 |
| `book` | 内蔵定跡の利用状況 | book fingerprint、勝率、先後別集計、out-of-book ply 分布 |

共通して、`Games` では保存済み対局を確認でき、`Live View` では進行中の対局盤面を見られます。

Book タブの out-of-book は「実着手が指定 book 上の候補手集合に含まれたか」を後から観測する指標です。エンジンが実際に book 由来で指したことを断定するものではありません。

`Live View` と対局詳細の timing では、持ち時間に課金された `wall_time_ms` と、engine の `think()` 呼び出しから `bestmove` 回収までを測った `engine_wall_time_ms` を分けて扱います。engine throughput や wall NPS の比較では `engine_wall_time_ms` を優先してください。

## run ディレクトリ

ダッシュボードは run ディレクトリ内の成果物を読みます。代表的なファイルは次の通りです。

```text
run/
├── game.db
├── manifest.json
├── state.json
├── data/
└── records/
```

保存済み run を `shogiarena dashboard serve` で開く場合、現行 CLI は run ディレクトリ直下の `game.db` を必要とします。自己対局生成などで dashboard を使う場合も、`game.db` を含む run ディレクトリを指定してください。

## うまく表示されないとき

- `--run-dir` が実際の run ディレクトリを指しているか確認する
- 実行中の dashboard と同じポートを別プロセスが使っていないか確認する
- ブラウザをリロードする
- run が古い場合は、現在の ShogiArena で再実行または再生成する

関連する確認コマンド:

```bash
shogiarena results summary /path/to/run
shogiarena results verify-provenance /path/to/run
```
