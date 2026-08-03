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

## ローカル限定のセキュリティ境界

v1 のダッシュボードは、認証を備えないローカルの single-user UI です。
`dashboard.api_host` は `localhost`、`127.0.0.0/8`、`::1` のいずれかに限定され、`0.0.0.0` や LAN のアドレスでは起動しません。
外部マシンからの閲覧、リバースプロキシ経由の公開、multi-user 運用には対応していません。
そのような運用では、将来の認証対応を待ち、ポートフォワーディング等で公開しないでください。

## 保存済み run を開く

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

`dashboard serve` は保存済み成果物を閲覧する read-only mode で起動します。
instance の作成・変更、schedule 操作、diagnostics snapshot の保存など、状態を変更する API は拒否されます。

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

Bookタブは、`BookFile`などでエンジン自身に設定した定跡ファイルごとの成績を比較する画面です。
ShogiArenaが`rules.initial_positions`で選ぶ開始局面は対象外であり、そちらはOpeningsタブに表示します。
エンジン内蔵定跡を使わなかったrunでは、分析操作を表示せず、この違いを説明する空状態を表示します。

Bookタブのout-of-bookは、「実着手が指定book上の候補手集合に含まれたか」を後から観測する指標です。
エンジンが実際に book 由来で指したことを断定するものではありません。

Book タブ内の `Pairs` subview では、schedule 上のペアごとに book prefix の一致状況、first diff ply、prefix match rate、`measurement_status` を確認できます。
ここで表示される prefix も、明示記録された `book_hit` または opt-in の book lookup membership に基づく diagnostic です。

`Live View` と対局詳細の timing では、持ち時間に課金された `wall_time_ms` と、engine の `think()` 呼び出しから `bestmove` 回収までを測った `engine_wall_time_ms` を分けて扱います。
engine throughput や wall NPS を比較するときは `engine_wall_time_ms` を使ってください。

## run ディレクトリ

ダッシュボードは run ディレクトリ内の成果物を読みます。
実行開始時にCLIが`Run directory: <absolute path>`を出力するため、このパスを保存済みrunの指定に使えます。
保存済みrunでは、`schedule.json`、`state.json`、`game.db`から対局一覧と完了数を読み取り専用で復元します。
代表的なファイルは次の通りです。

```text
run/
├── game.db
├── manifest.json
├── state.json
├── data/
└── records/
```

保存済み run を `shogiarena dashboard serve` で開く場合、現行 CLI は run ディレクトリ直下の `game.db` を必要とします。
自己対局生成などで dashboard を使う場合も、`game.db` を含む run ディレクトリを指定してください。

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
